"""Trusted boundary between untrusted AI signals, risk checks, and execution."""
from decimal import Decimal
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from app.execution import BaseExecutionAdapter
from app.risk_engine import RiskEngine
from app.trading_models import OrderSide, OrderType, ProposedOrder, RiskProfile


class AISignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1, max_length=64)
    symbol: str
    action: OrderSide
    amount: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=8)
    price: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=8)
    stop_loss_price: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=8)
    signal_id: str = Field(min_length=1, max_length=128)


class TradeOrchestrator:
    def __init__(self, risk_engine: RiskEngine, execution_adapter: BaseExecutionAdapter, audit_log: list[dict[str, Any]]):
        self.risk_engine = risk_engine
        self.execution = execution_adapter
        self.audit_log = audit_log

    async def handle_ai_signal(self, signal: AISignal, profile: RiskProfile, daily_loss: Decimal, balance: Decimal, *, authorized: bool, permitted_order_types: set[str], max_trade_risk: Decimal, data_age_seconds: int = 0) -> dict[str, Any]:
        order = ProposedOrder(agent_id=signal.agent_id, symbol=signal.symbol, side=signal.action, order_type=OrderType.LIMIT, quantity=signal.amount, price=signal.price, stop_loss_price=signal.stop_loss_price)
        result = self.risk_engine.evaluate_order(
            order, profile, daily_loss, balance, authorized=authorized,
            permitted_order_types=permitted_order_types,
            max_trade_risk=max_trade_risk,
            data_age_seconds=data_age_seconds,
        )
        if not result.passed:
            event = {"agent_id": order.agent_id, "symbol": order.symbol, "stage": "RISK_ENGINE_REJECTION", "error_code": result.rejection_reason.value}
            self.audit_log.append(event)
            return {"success": False, "error": result.rejection_reason.value, "stage": "RISK_ENGINE_REJECTION"}

        # Signal ID makes network retries deterministic. Adapter returns the original result for duplicates.
        key = f"{order.agent_id}:{signal.signal_id}"
        execution_result = await self.execution.place_order(order, key)
        self.audit_log.append({"agent_id": order.agent_id, "symbol": order.symbol, "stage": "EXECUTION", "execution_status": execution_result.get("status")})
        return {"success": execution_result.get("status") == "FILLED", "execution": execution_result, "stage": "EXECUTION_COMPLETE"}
