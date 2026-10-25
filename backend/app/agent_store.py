"""Durable agent lifecycle, immutable strategy revisions, and audit events."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import AuditLog, PaperAccount, StrategyRevision, TradingAgent
from app.indicator_engine import required_candles
from app.paper_store import PaperLedgerStore


LEGAL_TRANSITIONS = {
    "DRAFT": {"VALIDATING", "STOPPED"},
    "VALIDATING": {"AWAITING_APPROVAL", "DRAFT", "STOPPED"},
    "AWAITING_APPROVAL": {"PAPER_TRADING", "DRAFT", "STOPPED"},
    "PAPER_TRADING": {"PAUSED", "STOPPED"},
    "ACTIVE": {"PAUSED", "STOPPED"},
    "PAUSED": {"PAPER_TRADING", "ACTIVE", "STOPPED"},
    "STOPPED": set(),
}


class AgentNotFound(Exception):
    pass


class InvalidAgentTransition(Exception):
    pass


class AgentStore:
    def __init__(self, sessions: sessionmaker):
        self.sessions = sessions

    @staticmethod
    def serialize(agent: TradingAgent, strategy: dict, approval_status: str | None = None) -> dict:
        return {
            "id": agent.id, "name": agent.name, "state": agent.state,
            "strategy": strategy, "strategy_version": agent.strategy_version,
            "paper_account_id": agent.paper_account_id,
            "approval_status": approval_status or "DRAFT",
        }

    def create(self, user_id: str, name: str, strategy: dict) -> dict:
        account_id, agent_id = str(uuid4()), str(uuid4())
        initial = Decimal(str(strategy["allocated_capital"]))
        with self.sessions() as session, session.begin():
            account = PaperAccount(id=account_id, currency=strategy["currency"],
                                   user_id=user_id, available_balance=initial, reserved_balance=Decimal("0"), status="ACTIVE")
            agent = TradingAgent(id=agent_id, user_id=user_id, name=name, state="DRAFT", strategy_version=1, paper_account_id=account_id)
            session.add_all([account, agent])
            session.flush()
            if initial:
                PaperLedgerStore._write_ledger(session, account, f"funding:{account.id}", "INITIAL_FUNDING", [
                    ("CASH_AVAILABLE", "DEBIT", initial), ("PAPER_CAPITAL", "CREDIT", initial),
                ])
            session.add(StrategyRevision(agent_id=agent_id, version=1, strategy=strategy, approval_status="DRAFT"))
            session.add(AuditLog(agent_id=agent_id, event_type="AGENT_CREATED", details={"state": "DRAFT", "strategy_version": 1}))
            return self.serialize(agent, strategy)

    def get(self, agent_id: str, user_id: str) -> dict | None:
        with self.sessions() as session:
            query = select(TradingAgent).join(PaperAccount, PaperAccount.id == TradingAgent.paper_account_id).where(
                TradingAgent.id == agent_id, TradingAgent.user_id == user_id,
                PaperAccount.user_id == user_id,
            )
            agent = session.scalar(query)
            if agent is None:
                return None
            revision = session.scalar(select(StrategyRevision).where(
                StrategyRevision.agent_id == agent.id,
                StrategyRevision.version == agent.strategy_version,
            ))
            return self.serialize(agent, revision.strategy, revision.approval_status)

    def list(self, user_id: str) -> list[dict]:
        with self.sessions() as session:
            agents = session.scalars(select(TradingAgent).join(
                PaperAccount, PaperAccount.id == TradingAgent.paper_account_id,
            ).where(
                TradingAgent.user_id == user_id, PaperAccount.user_id == user_id,
            ).order_by(TradingAgent.created_at)).all()
            result = []
            for agent in agents:
                revision = session.scalar(select(StrategyRevision).where(
                    StrategyRevision.agent_id == agent.id,
                    StrategyRevision.version == agent.strategy_version,
                ))
                result.append(self.serialize(agent, revision.strategy, revision.approval_status))
            return result

    def transition(self, agent_id: str, user_id: str, target: str, explicit_approval: bool) -> dict:
        with self.sessions() as session, session.begin():
            agent = session.scalar(select(TradingAgent).join(
                PaperAccount, PaperAccount.id == TradingAgent.paper_account_id,
            ).where(
                TradingAgent.id == agent_id, TradingAgent.user_id == user_id,
                PaperAccount.user_id == user_id,
            ).with_for_update(of=TradingAgent))
            if agent is None:
                raise AgentNotFound(agent_id)
            if target == "ACTIVE":
                raise InvalidAgentTransition("Live execution is disabled in this build")
            if target == "PAPER_TRADING" and agent.state != "PAUSED":
                raise InvalidAgentTransition("Use the explicit strategy approval endpoint to start paper trading")
            if target not in LEGAL_TRANSITIONS.get(agent.state, set()):
                raise InvalidAgentTransition(f"Illegal state transition: {agent.state} to {target}")
            if target == "PAPER_TRADING" and not explicit_approval:
                raise InvalidAgentTransition("Explicit user approval is required")
            previous = agent.state
            agent.state = target
            session.add(AuditLog(agent_id=agent.id, event_type="AGENT_STATE_TRANSITION", details={"from": previous, "to": target}))
            revision = session.scalar(select(StrategyRevision).where(
                StrategyRevision.agent_id == agent.id,
                StrategyRevision.version == agent.strategy_version,
            ))
            session.flush()
            return self.serialize(agent, revision.strategy, revision.approval_status)

    def approve_strategy(self, agent_id: str, user_id: str) -> dict:
        now = datetime.now(timezone.utc)
        with self.sessions() as session, session.begin():
            agent = session.scalar(select(TradingAgent).where(
                TradingAgent.id == agent_id, TradingAgent.user_id == user_id,
            ).with_for_update())
            if agent is None:
                raise AgentNotFound(agent_id)
            if agent.state != "AWAITING_APPROVAL":
                raise InvalidAgentTransition("Agent must be awaiting approval")
            current = session.scalar(select(StrategyRevision).where(
                StrategyRevision.agent_id == agent.id,
                StrategyRevision.version == agent.strategy_version,
            ).with_for_update())
            if current is None:
                raise InvalidAgentTransition("Strategy revision is unavailable")
            strategy = current.strategy
            if not strategy.get("stop_loss_percent"):
                raise InvalidAgentTransition("An explicit stop-loss percentage is required before approval")
            supported_kinds = {"indicator_threshold", "price_cross", "price_change_percent", "volume_threshold"}
            for section in ("entry_conditions", "exit_conditions"):
                conditions = strategy.get(section, [])
                if not conditions or any(not isinstance(condition, dict) or condition.get("kind") not in supported_kinds for condition in conditions):
                    raise InvalidAgentTransition("Only structured, evaluatable conditions can be approved")
                if any(condition.get("indicator") not in {"PRICE", "VOLUME", "RSI", "MACD", "SMA", "EMA"} for condition in conditions):
                    raise InvalidAgentTransition("The selected indicator is not supported by the deterministic paper evaluator")
                if any(condition.get("symbol", "").replace("/", "") != strategy.get("symbol", "").replace("/", "") for condition in conditions):
                    raise InvalidAgentTransition("Every approved condition must use the agent's configured market")
                if any(condition.get("timeframe") not in {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"} for condition in conditions):
                    raise InvalidAgentTransition("Every approved condition requires a supported timeframe")
                if any(condition.get("kind") == "price_cross" and (condition.get("indicator") != "PRICE" or condition.get("operator") not in {"crosses_above", "crosses_below"}) for condition in conditions):
                    raise InvalidAgentTransition("Price-cross rules require an explicit direction")
                if any(condition.get("kind") == "price_change_percent" and condition.get("indicator") != "PRICE" for condition in conditions):
                    raise InvalidAgentTransition("Price-change rules must use the price indicator")
                if any(condition.get("kind") == "price_change_percent" and condition.get("operator") in {"crosses_above", "crosses_below"} for condition in conditions):
                    raise InvalidAgentTransition("Percentage-change rules require a direct comparison operator")
                if any(condition.get("kind") == "volume_threshold" and condition.get("indicator") != "VOLUME" for condition in conditions):
                    raise InvalidAgentTransition("Volume rules must use the volume indicator")
                for condition in conditions:
                    indicator = condition["indicator"]
                    if indicator in {"RSI", "SMA", "EMA", "MACD"} and condition.get("kind") != "indicator_threshold":
                        raise InvalidAgentTransition("Technical indicators require indicator_threshold conditions")
                    if indicator in {"RSI", "SMA", "EMA"}:
                        period = condition.get("period")
                        if not isinstance(period, int) or not 2 <= period <= 100:
                            raise InvalidAgentTransition(f"{indicator} requires an explicit period from 2 to 100")
                        if any(condition.get(field) is not None for field in ("fast_period", "slow_period", "signal_period")):
                            raise InvalidAgentTransition(f"{indicator} accepts only its period parameter")
                    elif indicator == "MACD":
                        fast, slow, signal = (condition.get(field) for field in ("fast_period", "slow_period", "signal_period"))
                        if not (isinstance(fast, int) and isinstance(slow, int) and isinstance(signal, int)
                                and 2 <= fast <= 50 and fast < slow <= 100 and 2 <= signal <= 50):
                            raise InvalidAgentTransition("MACD requires explicit fast, slow, and signal periods")
                        if condition.get("period") is not None:
                            raise InvalidAgentTransition("MACD does not use a single period parameter")
                    elif any(condition.get(field) is not None for field in ("period", "fast_period", "slow_period", "signal_period")):
                        raise InvalidAgentTransition("Price and volume rules cannot include indicator periods")
                    if required_candles(condition) + 1 > 200:
                        raise InvalidAgentTransition("Indicator periods exceed the exchange candle-history limit")
                for condition in conditions:
                    try:
                        threshold = Decimal(str(condition.get("value", "")))
                    except InvalidOperation:
                        raise InvalidAgentTransition("Every approved condition requires a numeric threshold") from None
                    if not threshold.is_finite():
                        raise InvalidAgentTransition("Every approved condition requires a finite numeric threshold")
                    if condition.get("indicator") in {"PRICE", "VOLUME", "RSI", "SMA", "EMA"} and threshold <= 0:
                        raise InvalidAgentTransition("Price and volume thresholds must be positive")
                    if condition.get("indicator") == "RSI" and threshold > 100:
                        raise InvalidAgentTransition("RSI thresholds must be between 0 and 100")
            next_version = agent.strategy_version + 1
            session.add(StrategyRevision(
                agent_id=agent.id, version=next_version, strategy=strategy,
                approval_status="APPROVED", approved_by_user_id=user_id, approved_at=now,
            ))
            previous = agent.state
            agent.strategy_version = next_version
            agent.state = "PAPER_TRADING"
            session.add(AuditLog(agent_id=agent.id, event_type="STRATEGY_APPROVED", details={
                "from_version": next_version - 1, "approved_version": next_version,
                "state_from": previous, "state_to": "PAPER_TRADING", "mode": "paper",
            }))
            session.flush()
            return self.serialize(agent, strategy, "APPROVED")
