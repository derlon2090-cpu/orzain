"""Execution adapters. Live adapter intentionally refuses all orders until implemented."""
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

from app.trading_models import ProposedOrder


class BaseExecutionAdapter(ABC):
    @abstractmethod
    async def place_order(self, order: ProposedOrder, idempotency_key: str) -> dict[str, Any]:
        """Submit once, using a stable idempotency key for retry safety."""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an outstanding order if supported."""


class PaperTradingEngine(BaseExecutionAdapter):
    def __init__(self, simulated_balance: Decimal, fee_rate: Decimal = Decimal("0.001"), slippage_rate: Decimal = Decimal("0.0005")):
        if simulated_balance < 0 or fee_rate < 0 or slippage_rate < 0:
            raise ValueError("Balance, fee, and slippage must be non-negative Decimals")
        self.balance = simulated_balance
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.executed_orders: dict[str, dict[str, Any]] = {}
        self.processed_idempotency_keys: dict[str, dict[str, Any]] = {}
        self.inventory: dict[str, Decimal] = {}
        self.cost_basis: dict[str, Decimal] = {}
        self.daily_net_pnl = Decimal("0")
        self._utc_day = datetime.now(timezone.utc).date()

    def current_daily_loss(self) -> Decimal:
        day = datetime.now(timezone.utc).date()
        if day != self._utc_day:
            self._utc_day = day
            self.daily_net_pnl = Decimal("0")
        return max(Decimal("0"), -self.daily_net_pnl)

    async def place_order(self, order: ProposedOrder, idempotency_key: str) -> dict[str, Any]:
        if not idempotency_key:
            return {"status": "REJECTED", "reason": "IDEMPOTENCY_KEY_REQUIRED"}
        if idempotency_key in self.processed_idempotency_keys:
            # Return the original result on retries; never create another simulated fill.
            return self.processed_idempotency_keys[idempotency_key]

        direction = Decimal("1") if order.side.value == "BUY" else Decimal("-1")
        executed_price = order.price * (Decimal("1") + direction * self.slippage_rate)
        cost = order.quantity * executed_price
        fee = cost * self.fee_rate
        if order.side.value == "BUY":
            total = cost + fee
            if self.balance < total:
                result = {"status": "REJECTED", "reason": "INSUFFICIENT_PAPER_FUNDS"}
                self.processed_idempotency_keys[idempotency_key] = result
                return result
            self.balance -= total
            self.inventory[order.symbol] = self.inventory.get(order.symbol, Decimal("0")) + order.quantity
            self.cost_basis[order.symbol] = self.cost_basis.get(order.symbol, Decimal("0")) + total
        else:
            held = self.inventory.get(order.symbol, Decimal("0"))
            if held < order.quantity:
                result = {"status": "REJECTED", "reason": "INSUFFICIENT_PAPER_POSITION"}
                self.processed_idempotency_keys[idempotency_key] = result
                return result
            self.balance += cost - fee
            allocated_basis = self.cost_basis[order.symbol] * order.quantity / held
            self.cost_basis[order.symbol] -= allocated_basis
            realized_pnl = cost - fee - allocated_basis
            self.current_daily_loss()  # Roll the UTC accounting day before adding today's result.
            self.daily_net_pnl += realized_pnl
            self.inventory[order.symbol] = held - order.quantity

        record = {
            "order_id": str(uuid.uuid4()), "symbol": order.symbol,
            "side": order.side.value, "executed_price": str(executed_price),
            "executed_qty": str(order.quantity), "fee": str(fee),
            "status": "FILLED", "mode": "PAPER",
            "remaining_balance": str(self.balance),
            "realized_pnl": str(realized_pnl) if order.side.value == "SELL" else "0",
            "daily_loss": str(self.current_daily_loss()),
        }
        self.executed_orders[record["order_id"]] = record
        self.processed_idempotency_keys[idempotency_key] = record
        return record

    async def cancel_order(self, order_id: str) -> bool:
        return False  # Paper fills are immediate; there are no open orders to cancel.


class LiveExchangeAdapter(BaseExecutionAdapter):
    """Fail-closed placeholder. It does not connect to any exchange."""
    async def place_order(self, order: ProposedOrder, idempotency_key: str) -> dict[str, Any]:
        return {"status": "REJECTED", "reason": "LIVE_EXECUTION_DISABLED"}

    async def cancel_order(self, order_id: str) -> bool:
        return False
