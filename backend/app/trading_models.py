"""Data contracts shared by the risk and execution orchestration layers."""
from decimal import Decimal
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class ProposedOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,12}/[A-Z0-9]{2,12}$")
    side: OrderSide
    order_type: OrderType
    quantity: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=8)
    price: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=8)
    stop_loss_price: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=8)


class RiskProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_capital_per_order: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=4)
    max_daily_loss: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=4)
    allowed_symbols: list[str] = Field(min_length=1)
    allow_leverage: bool = False
    emergency_stop: bool = False


class RiskErrorCode(str, Enum):
    EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
    SYMBOL_NOT_PERMITTED = "SYMBOL_NOT_PERMITTED"
    MAX_ORDER_CAPITAL_EXCEEDED = "MAX_ORDER_CAPITAL_EXCEEDED"
    INSUFFICIENT_UNLEVERAGED_BALANCE = "INSUFFICIENT_UNLEVERAGED_BALANCE"
    DAILY_DRAWDOWN_LIMIT_REACHED = "DAILY_DRAWDOWN_LIMIT_REACHED"
    AGENT_NOT_AUTHORIZED = "AGENT_NOT_AUTHORIZED"
    ORDER_TYPE_NOT_PERMITTED = "ORDER_TYPE_NOT_PERMITTED"
    TRADE_RISK_LIMIT_EXCEEDED = "TRADE_RISK_LIMIT_EXCEEDED"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    STOP_LOSS_INVALID = "STOP_LOSS_INVALID"


class RiskCheckResult(BaseModel):
    passed: bool
    rejection_reason: RiskErrorCode | None = None
