"""Single-process paper signal worker. Only approved rules and the paper ledger are executable."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import time

from sqlalchemy import select

from app.credential_crypto import CredentialEncryptionError, decrypt_credential
from app.exchanges.binance_testnet import BinanceTestnetAdapter, ExchangeError
from app.indicator_engine import condition_values, required_candles
from app.models import AgentSignalState, ExchangeCredential, PaperAccount, PaperPosition, StrategyRevision, TradingAgent
from app.paper_store import PaperLedgerStore
from app.risk_engine import RiskEngine
from app.trading_models import OrderSide, OrderType, ProposedOrder, RiskProfile


def _condition_value(condition: dict, bars: list[dict], live_price: Decimal) -> tuple[Decimal, Decimal]:
    previous, current = bars[-2], bars[-1]
    kind, indicator = condition["kind"], condition["indicator"]
    if kind == "price_change_percent":
        if previous["close"] == 0:
            raise ValueError("invalid_previous_close")
        return Decimal("0"), (current["close"] - previous["close"]) / previous["close"] * Decimal("100")
    if indicator == "PRICE":
        return previous["close"], live_price
    if indicator == "VOLUME":
        return previous["volume"], current["volume"]
    if indicator in {"RSI", "MACD", "SMA", "EMA"}:
        return condition_values(condition, bars)
    raise ValueError("unsupported_indicator")


def _matches(condition: dict, previous: Decimal, current: Decimal) -> bool:
    try:
        threshold = Decimal(condition["value"])
    except Exception:
        raise ValueError("invalid_threshold") from None
    operator = condition["operator"]
    if operator == "gt":
        return current > threshold
    if operator == "gte":
        return current >= threshold
    if operator == "lt":
        return current < threshold
    if operator == "lte":
        return current <= threshold
    if operator == "crosses_above":
        return previous < threshold <= current
    if operator == "crosses_below":
        return previous > threshold >= current
    raise ValueError("unsupported_operator")


class PaperSignalEvaluator:
    def __init__(self, store: PaperLedgerStore, risk_engine: RiskEngine | None = None):
        self.store = store
        self.risk_engine = risk_engine or RiskEngine()

    def run_once(self) -> dict[str, int]:
        with self.store.sessions() as session:
            rows = session.execute(
                select(TradingAgent, StrategyRevision).join(
                    StrategyRevision,
                    (StrategyRevision.agent_id == TradingAgent.id)
                    & (StrategyRevision.version == TradingAgent.strategy_version),
                ).where(TradingAgent.state == "PAPER_TRADING", StrategyRevision.approval_status == "APPROVED")
            ).all()
            agents = [(agent.id, agent.user_id, agent.paper_account_id, dict(revision.strategy), revision.version)
                      for agent, revision in rows]
        totals = {"checked": 0, "orders": 0, "rejected": 0, "skipped": 0, "errors": 0}
        for agent_id, user_id, account_id, strategy, version in agents:
            totals["checked"] += 1
            try:
                outcome = self.evaluate_agent(agent_id, user_id, account_id, strategy, version)
                if outcome.get("status") == "FILLED":
                    totals["orders"] += 1
                elif outcome.get("status") == "REJECTED":
                    totals["rejected"] += 1
                else:
                    totals["skipped"] += 1
            except Exception as exc:
                # A network/client failure for one agent must not stop other agents
                # or the long-running worker. No order is recorded on this path.
                self._mark(agent_id, "ERROR", None, getattr(exc, "code", type(exc).__name__))
                totals["skipped"] += 1
                totals["errors"] += 1
        return totals

    def evaluate_agent(self, agent_id: str, user_id: str, account_id: str, strategy: dict, version: int) -> dict:
        adapter = self._adapter_for_user(user_id)
        entry = strategy.get("entry_conditions", [])
        exits = strategy.get("exit_conditions", [])
        conditions = entry + exits
        if not conditions or any(not isinstance(rule, dict) for rule in conditions):
            raise ValueError("structured_conditions_required")
        symbol = strategy["symbol"].replace("/", "").upper()
        candle_requirements: dict[str, int] = {}
        for rule in conditions:
            frame = rule["timeframe"]
            candle_requirements[frame] = max(candle_requirements.get(frame, 2), required_candles(rule))
        if any(count > 200 for count in candle_requirements.values()):
            raise ValueError("indicator_candle_limit_exceeded")
        timeframes = sorted(candle_requirements)
        bars_by_timeframe: dict[str, list[dict]] = {}
        for timeframe in timeframes:
            candles = adapter.get_klines(symbol, timeframe, limit=max(3, candle_requirements[timeframe] + 1))
            closed = [bar for bar in candles if bar["close_time"] < int(time.time() * 1000)]
            if len(closed) < candle_requirements[timeframe]:
                raise ValueError("insufficient_closed_candles")
            bars_by_timeframe[timeframe] = closed
        ticker = adapter.get_market_price(symbol)
        live_price = Decimal(ticker["price"])
        sample_key = ":".join(f"{frame}:{bars[-1]['close_time']}" for frame, bars in sorted(bars_by_timeframe.items()))
        event_id = hashlib.sha256(f"{agent_id}:{version}:{sample_key}".encode()).hexdigest()[:32]

        with self.store.sessions() as session:
            account = session.get(PaperAccount, account_id)
            position = session.scalar(select(PaperPosition).where(
                PaperPosition.account_id == account_id,
                PaperPosition.symbol == strategy["symbol"],
            ))
            if account is None or account.status != "ACTIVE":
                raise ValueError("paper_account_unavailable")
            balance = Decimal(account.available_balance)
            daily_loss = max(Decimal("0"), -Decimal(account.daily_net_pnl))
            position_qty = Decimal(position.quantity) if position else Decimal("0")
            average_cost = Decimal(position.cost_basis) / position_qty if position and position_qty else Decimal("0")

        entry_trigger = all(self._condition_matches(rule, bars_by_timeframe, live_price) for rule in entry)
        exit_trigger = all(self._condition_matches(rule, bars_by_timeframe, live_price) for rule in exits)
        stop_percent = Decimal(str(strategy["stop_loss_percent"])) / Decimal("100")
        stop_trigger = bool(position_qty and live_price <= average_cost * (Decimal("1") - stop_percent))
        side = "SELL" if position_qty and (exit_trigger or stop_trigger) else "BUY" if not position_qty and entry_trigger else None
        if side is None:
            self._mark(agent_id, "MONITORING", event_id, None)
            return {"status": "MONITORING", "event_id": event_id}

        order_type = OrderType.MARKET if "market" in strategy["allowed_order_types"] else OrderType.LIMIT
        if side == "SELL":
            quantity = position_qty
            stop_price = average_cost * (Decimal("1") - stop_percent)
            reduce_only = True
        else:
            allocation = Decimal(str(strategy["allocated_capital"]))
            max_trade_risk = allocation * Decimal(str(strategy["max_risk_per_trade_percent"])) / Decimal("100")
            stop_price = live_price * (Decimal("1") - stop_percent)
            if stop_price <= 0 or live_price <= 0:
                raise ValueError("invalid_market_price")
            raw_quantity = min(
                max_trade_risk / (live_price - stop_price),
                allocation / live_price,
                balance / (live_price * (Decimal("1") + self.store.slippage_rate) * (Decimal("1") + self.store.fee_rate)),
            )
            quantity_rules = adapter.validate_quantity(symbol, raw_quantity)
            step = Decimal(quantity_rules["step_size"])
            if step <= 0:
                raise ValueError("invalid_exchange_step_size")
            quantity = (raw_quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
            quantity_rules = adapter.validate_quantity(symbol, quantity)
            if not quantity_rules["valid"]:
                result = self._record_rejection(account_id, agent_id, strategy, version, side, order_type, quantity or raw_quantity, live_price, stop_price, event_id, quantity_rules["reason"])
                self._mark(agent_id, result["status"], event_id, quantity_rules["reason"])
                return result
            price_filter = adapter.validate_price(symbol, stop_price)
            tick = Decimal(price_filter.get("tick_size", "0"))
            if tick > 0:
                stop_price = (stop_price / tick).to_integral_value(rounding=ROUND_DOWN) * tick
            stop_rules = adapter.validate_price(symbol, stop_price)
            if not stop_rules["valid"]:
                result = self._record_rejection(account_id, agent_id, strategy, version, side, order_type, quantity, live_price, stop_price, event_id, stop_rules["reason"])
                self._mark(agent_id, result["status"], event_id, stop_rules["reason"])
                return result
            reduce_only = False

        proposed = ProposedOrder(
            agent_id=agent_id, symbol=strategy["symbol"], side=OrderSide(side), order_type=order_type,
            quantity=quantity, price=live_price, stop_loss_price=stop_price,
        )
        risk_profile = RiskProfile(
            max_capital_per_order=Decimal(str(strategy["allocated_capital"])),
            max_daily_loss=Decimal(str(strategy["allocated_capital"])) * Decimal(str(strategy["max_daily_loss_percent"])) / Decimal("100"),
            allowed_symbols=[strategy["symbol"]], allow_leverage=False, emergency_stop=False,
        )
        trade_risk = None if reduce_only else Decimal(str(strategy["allocated_capital"])) * Decimal(str(strategy["max_risk_per_trade_percent"])) / Decimal("100")
        check = self.risk_engine.evaluate_order(
            proposed, risk_profile, daily_loss, balance, authorized=True,
            permitted_order_types={value.upper() for value in strategy["allowed_order_types"]},
            max_trade_risk=trade_risk, data_age_seconds=0, reduce_only=reduce_only,
        )
        idempotency_key = f"agent:{agent_id}:v{version}:{side.lower()}:{event_id}"
        if not check.passed:
            result = self.store.record_rejection(account_id, proposed, idempotency_key, check.rejection_reason.value)
        else:
            result = self.store.place_order(account_id, proposed, idempotency_key)
        self._mark(agent_id, result["status"], event_id, result.get("rejection_code"), side)
        return result

    @staticmethod
    def _condition_matches(rule: dict, bars_by_timeframe: dict[str, list[dict]], live_price: Decimal) -> bool:
        bars = bars_by_timeframe[rule["timeframe"]]
        previous, current = _condition_value(rule, bars, live_price)
        return _matches(rule, previous, current)

    def _adapter_for_user(self, user_id: str) -> BinanceTestnetAdapter:
        with self.store.sessions() as session:
            records = session.scalars(select(ExchangeCredential).where(
                ExchangeCredential.user_id == user_id,
                ExchangeCredential.exchange == "binance",
            ).order_by(ExchangeCredential.created_at)).all()
            for record in records:
                try:
                    bundle = json.loads(decrypt_credential(record.ciphertext, user_id=user_id, credential_id=record.id))
                except (CredentialEncryptionError, json.JSONDecodeError):
                    continue
                if bundle.get("environment") == "sandbox":
                    return BinanceTestnetAdapter(bundle["api_key"], bundle["api_secret"])
        raise ValueError("sandbox_exchange_connection_missing")

    def _record_rejection(self, account_id, agent_id, strategy, version, side, order_type, quantity, price, stop_price, event_id, code):
        proposed = ProposedOrder(agent_id=agent_id, symbol=strategy["symbol"], side=OrderSide(side), order_type=order_type,
                                 quantity=max(quantity, Decimal("0.00000001")), price=price, stop_loss_price=stop_price)
        return self.store.record_rejection(account_id, proposed, f"agent:{agent_id}:v{version}:{side.lower()}:{event_id}", code)

    def _mark(self, agent_id: str, status: str, event_id: str | None, error_code: str | None, signal: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        with self.store.sessions() as session, session.begin():
            state = session.get(AgentSignalState, agent_id, with_for_update=True)
            if state is None:
                state = AgentSignalState(agent_id=agent_id, last_status=status, last_event_id=event_id,
                                         last_signal=signal, last_error_code=error_code, updated_at=now)
                session.add(state)
            else:
                state.last_status, state.last_event_id = status, event_id
                state.last_signal, state.last_error_code, state.updated_at = signal, error_code, now
