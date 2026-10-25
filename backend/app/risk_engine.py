"""Pure deterministic risk pipeline. All financial arithmetic uses Decimal."""
from decimal import Decimal
from app.trading_models import ProposedOrder, RiskCheckResult, RiskErrorCode, RiskProfile


class RiskEngine:
    def evaluate_order(
        self,
        order: ProposedOrder,
        profile: RiskProfile,
        current_daily_loss: Decimal,
        free_balance: Decimal,
        *,
        authorized: bool = True,
        permitted_order_types: set[str] | None = None,
        max_trade_risk: Decimal | None = None,
        data_age_seconds: int = 0,
        reduce_only: bool = False,
    ) -> RiskCheckResult:
        """Run every mandatory gate in order and return the first stable error code."""
        notional = order.quantity * order.price
        stop_is_invalid = not reduce_only and ((order.side.value == "BUY" and order.stop_loss_price >= order.price) or (order.side.value == "SELL" and order.stop_loss_price <= order.price))
        estimated_trade_risk = abs(order.price - order.stop_loss_price) * order.quantity
        checks = [
            (profile.emergency_stop and not reduce_only, RiskErrorCode.EMERGENCY_STOP_ACTIVE),
            (not authorized, RiskErrorCode.AGENT_NOT_AUTHORIZED),
            (order.symbol not in profile.allowed_symbols, RiskErrorCode.SYMBOL_NOT_PERMITTED),
            (permitted_order_types is not None and order.order_type.value not in permitted_order_types, RiskErrorCode.ORDER_TYPE_NOT_PERMITTED),
            (stop_is_invalid, RiskErrorCode.STOP_LOSS_INVALID),
            (not reduce_only and notional > profile.max_capital_per_order, RiskErrorCode.MAX_ORDER_CAPITAL_EXCEEDED),
            (not reduce_only and not profile.allow_leverage and order.side.value == "BUY" and notional > free_balance, RiskErrorCode.INSUFFICIENT_UNLEVERAGED_BALANCE),
            (not reduce_only and current_daily_loss >= profile.max_daily_loss, RiskErrorCode.DAILY_DRAWDOWN_LIMIT_REACHED),
            (not reduce_only and max_trade_risk is not None and estimated_trade_risk > max_trade_risk, RiskErrorCode.TRADE_RISK_LIMIT_EXCEEDED),
            (data_age_seconds > 15, RiskErrorCode.STALE_MARKET_DATA),
        ]
        for failed, code in checks:
            if failed:
                return RiskCheckResult(passed=False, rejection_reason=code)
        return RiskCheckResult(passed=True)
