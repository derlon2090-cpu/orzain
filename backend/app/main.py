"""Risk-first API foundation. Demo and paper execution only; no live exchange adapter."""
from __future__ import annotations

import os
import json
import logging
from decimal import Decimal
from enum import Enum
import smtplib
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr
from fastapi import Header
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from uuid import uuid4
from app.orchestrator import AISignal, TradeOrchestrator
from app.risk_engine import RiskEngine
from app.trading_models import ProposedOrder, RiskProfile
from app.db import SessionLocal
from app.paper_store import IdempotencyConflict, PaperLedgerStore, PersistentPaperExecutionAdapter
from app.agent_store import AgentNotFound, AgentStore, InvalidAgentTransition
from app.identity import EmailAlreadyRegistered, IdentityStore, InvalidCredentials, PasswordResetError, VerificationError
from app.email_delivery import EmailDeliveryConfigurationError, EmailService, assert_email_delivery_configured, send_verification_email
from app.models import ExchangeCredential
from app.credential_crypto import CredentialEncryptionError, decrypt_credential, encrypt_credential
from app.exchanges.binance_testnet import BinanceTestnetAdapter, ExchangeError
from app.rate_limit import limit_login, limit_register, limit_resend, limit_verify, limit_password_reset
from app.rate_limit import limit_ai_interpretation
from app.ai_strategy import (
    AIProviderNotConfigured, AIProviderUnavailable, StructuredStrategy, StrategyCondition,
    get_ai_provider, validate_interpretation,
)

app = FastAPI(title="TRADEGEN AI API", version="0.1.0", description="Demo and paper trading API. Live execution is disabled.")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","), allow_credentials=True, allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type", "Authorization", "Idempotency-Key"])


class AgentState(str, Enum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    PAPER_TRADING = "PAPER_TRADING"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,12}/[A-Z0-9]{2,12}$")
    market_type: Literal["spot"] = "spot"
    allocated_capital: Decimal = Field(gt=0, le=Decimal("1000000"), max_digits=16, decimal_places=4)
    currency: str = Field(default="USDT", pattern=r"^[A-Z]{2,8}$")
    leverage_allowed: Literal[False] = False
    max_risk_per_trade_percent: Decimal = Field(gt=0, le=Decimal("5"), max_digits=5, decimal_places=2)
    max_daily_loss_percent: Decimal = Field(gt=0, le=Decimal("20"), max_digits=5, decimal_places=2)
    entry_conditions: list[str | StrategyCondition] = Field(min_length=1, max_length=10)
    exit_conditions: list[str | StrategyCondition] = Field(min_length=1, max_length=10)
    stop_loss_required: Literal[True] = True
    stop_loss_percent: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("20"), max_digits=5, decimal_places=2)
    max_open_positions: int = Field(default=1, ge=1, le=10)
    allowed_order_types: list[Literal["market", "limit"]] = Field(min_length=1)
    execution_mode: Literal["paper"] = "paper"

class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    strategy: StrategySpec


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: SecretStr = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: SecretStr


class EmailVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=128)


class VerificationResendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr


class PasswordResetCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)


class ExchangeConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exchange: str = Field(pattern=r"^[a-z0-9_-]{2,32}$")
    label: str = Field(min_length=1, max_length=64)
    api_key: SecretStr = Field(min_length=1, max_length=512)
    api_secret: SecretStr = Field(min_length=1, max_length=1024)
    passphrase: SecretStr | None = Field(default=None, max_length=512)
    environment: Literal["sandbox"] = "sandbox"


class StrategyInterpretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=4000)
    exchange_connection_id: str | None = Field(default=None, min_length=36, max_length=36)


class OrderProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"]
    quantity: Decimal = Field(gt=0, max_digits=20, decimal_places=8)
    reference_price: Decimal = Field(gt=0, max_digits=20, decimal_places=8)
    stop_loss_price: Decimal = Field(gt=0, max_digits=20, decimal_places=8)
    estimated_daily_loss: Decimal = Field(default=Decimal("0"), ge=0, max_digits=16, decimal_places=4)
    open_positions: int = Field(default=0, ge=0)
    data_age_seconds: int = Field(default=0, ge=0)


class TransitionRequest(BaseModel):
    target: AgentState
    explicit_approval: bool = False


class AgentApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explicit_approval: Literal[True]


AUDIT_LOG: list[dict] = []
RISK_ENGINE = RiskEngine()
PAPER_STORE = PaperLedgerStore(SessionLocal)


def get_paper_store() -> PaperLedgerStore:
    return PAPER_STORE


def get_agent_store(store: PaperLedgerStore = Depends(get_paper_store)) -> AgentStore:
    return AgentStore(store.sessions)


def get_identity_store(store: PaperLedgerStore = Depends(get_paper_store)) -> IdentityStore:
    return IdentityStore(store.sessions)


def get_current_user(request: Request, identity: IdentityStore = Depends(get_identity_store)) -> dict:
    user = identity.current_user(request.cookies.get("tradegen_session"))
    if user is None:
        raise HTTPException(401, "Authentication required")
    return user


def verify_agent_ownership(agent_id: str, user: dict = Depends(get_current_user), agents: AgentStore = Depends(get_agent_store)) -> dict:
    agent = agents.get(agent_id, user["id"])
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return agent


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site request rejected")
        return
    allowed = {item.strip().rstrip("/") for item in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if item.strip()}
    public_origin = os.getenv("PUBLIC_ORIGIN")
    if public_origin:
        allowed.add(public_origin.rstrip("/"))
    if origin.rstrip("/") not in allowed:
        raise HTTPException(403, "Request origin is not allowed")


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="tradegen_session", value=token, httponly=True,
        secure=os.getenv("APP_ENV", "development").lower() == "production",
        samesite="strict", max_age=12 * 60 * 60, path="/api/v1",
    )


@app.get("/health")
def health():
    return {"status": "ok", "mode": "demo", "live_execution": False}


@app.get("/api/v1/exchanges")
def exchanges():
    return [{"id": "demo", "name": "Demo workspace", "available": True, "live_orders": False}]


@app.post("/api/v1/auth/register", status_code=201, dependencies=[Depends(require_same_origin)])
def register(payload: RegistrationRequest, request: Request, identity: IdentityStore = Depends(get_identity_store)):
    limit_register(request, str(payload.email))
    production = os.getenv("APP_ENV", "development").lower() == "production"
    if production:
        try:
            assert_email_delivery_configured()
        except EmailDeliveryConfigurationError as exc:
            raise HTTPException(503, str(exc)) from exc
    try:
        user, verification_token = identity.register(payload.email, payload.password.get_secret_value())
    except EmailAlreadyRegistered as exc:
        raise HTTPException(409, "An account with this email already exists") from exc
    if production:
        try:
            send_verification_email(user["email"], verification_token)
        except (EmailDeliveryConfigurationError, OSError, smtplib.SMTPException, ValueError) as exc:
            raise HTTPException(503, "Verification email could not be delivered; request a resend") from exc
    else:
        EmailService(production=False).send_verification(user["email"], verification_token)
    result = {"user": user, "status": "verification_required"}
    if not production:
        result["verification_token"] = verification_token
    return result


@app.post("/api/v1/auth/resend-verification", dependencies=[Depends(require_same_origin)])
def resend_verification(payload: VerificationResendRequest, request: Request, identity: IdentityStore = Depends(get_identity_store)):
    limit_resend(request, str(payload.email))
    production = os.getenv("APP_ENV", "development").lower() == "production"
    if production:
        try:
            assert_email_delivery_configured()
        except EmailDeliveryConfigurationError as exc:
            raise HTTPException(503, str(exc)) from exc
    token = identity.issue_verification(payload.email)
    if token and production:
        try:
            send_verification_email(payload.email, token)
        except (EmailDeliveryConfigurationError, OSError, smtplib.SMTPException, ValueError) as exc:
            raise HTTPException(503, "Verification email could not be delivered") from exc
    elif token:
        EmailService(production=False).send_verification(payload.email, token)
    result = {"status": "if_account_needs_verification_email_was_sent"}
    if token and not production:
        result["verification_token"] = token
    return result


@app.post("/api/v1/auth/verify-email", dependencies=[Depends(require_same_origin)])
def verify_email(payload: EmailVerificationRequest, request: Request, identity: IdentityStore = Depends(get_identity_store)):
    limit_verify(request, payload.token)
    try:
        return identity.verify_email(payload.token)
    except VerificationError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/v1/auth/password-reset/request", dependencies=[Depends(require_same_origin)])
def request_password_reset(payload: PasswordResetRequest, request: Request, identity: IdentityStore = Depends(get_identity_store)):
    limit_password_reset(request)
    token = identity.request_password_reset(str(payload.email))
    if token:
        try:
            EmailService().send_password_reset(str(payload.email), token)
        except (EmailDeliveryConfigurationError, OSError, smtplib.SMTPException, ValueError) as exc:
            # Keep both response and status indistinguishable for known and unknown accounts.
            logging.getLogger("tradegen.email").error("Password reset delivery failed: %s", type(exc).__name__)
    result = {"status": "if_account_exists_reset_email_was_sent"}
    if token and os.getenv("APP_ENV", "development").lower() != "production":
        result["reset_token"] = token
    return result


@app.post("/api/v1/auth/password-reset/complete", dependencies=[Depends(require_same_origin)])
def complete_password_reset(payload: PasswordResetCompleteRequest, request: Request, identity: IdentityStore = Depends(get_identity_store)):
    limit_password_reset(request, completion=True)
    try:
        identity.complete_password_reset(payload.token, payload.new_password.get_secret_value())
    except PasswordResetError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "password_updated"}


@app.post("/api/v1/auth/login", dependencies=[Depends(require_same_origin)])
def login(payload: LoginRequest, request: Request, response: Response, identity: IdentityStore = Depends(get_identity_store)):
    limit_login(request, str(payload.email))
    try:
        user, token = identity.login(payload.email, payload.password.get_secret_value())
    except InvalidCredentials as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Cookie"}) from exc
    except VerificationError as exc:
        raise HTTPException(403, str(exc)) from exc
    set_session_cookie(response, token)
    return {"user": user}


@app.post("/api/v1/auth/logout", dependencies=[Depends(require_same_origin)])
def logout(request: Request, response: Response, identity: IdentityStore = Depends(get_identity_store)):
    identity.logout(request.cookies.get("tradegen_session"))
    response.delete_cookie("tradegen_session", path="/api/v1", httponly=True,
                           secure=os.getenv("APP_ENV", "development").lower() == "production", samesite="strict")
    return {"status": "signed_out"}


@app.get("/api/v1/auth/me")
def who_am_i(user: dict = Depends(get_current_user)):
    return user


@app.post("/api/v1/exchange-connections", status_code=201, dependencies=[Depends(require_same_origin)])
def create_exchange_connection(payload: ExchangeConnectionCreate, user: dict = Depends(get_current_user), store: PaperLedgerStore = Depends(get_paper_store)):
    credential_id = str(uuid4())
    secret_bundle = {
        "api_key": payload.api_key.get_secret_value(),
        "api_secret": payload.api_secret.get_secret_value(),
        "passphrase": payload.passphrase.get_secret_value() if payload.passphrase else None,
        "environment": payload.environment,
    }
    try:
        ciphertext = encrypt_credential(json.dumps(secret_bundle, separators=(",", ":")), user_id=user["id"], credential_id=credential_id)
    except CredentialEncryptionError as exc:
        raise HTTPException(503, "Credential encryption is not configured") from exc
    try:
        with store.sessions() as session, session.begin():
            record = ExchangeCredential(id=credential_id, user_id=user["id"], exchange=payload.exchange,
                                        label=payload.label, ciphertext=ciphertext)
            session.add(record)
            session.flush()
            return {"id": record.id, "exchange": record.exchange, "label": record.label,
                    "environment": "sandbox", "api_key_status": "configured", "masked": "••••••",
                    "created_at": record.created_at.isoformat()}
    except IntegrityError as exc:
        raise HTTPException(409, "A connection with this exchange and label already exists") from exc


@app.get("/api/v1/exchange-connections")
def list_exchange_connections(user: dict = Depends(get_current_user), store: PaperLedgerStore = Depends(get_paper_store)):
    with store.sessions() as session:
        records = session.scalars(select(ExchangeCredential).where(ExchangeCredential.user_id == user["id"]).order_by(ExchangeCredential.created_at.desc())).all()
        return [{"id": item.id, "exchange": item.exchange, "label": item.label, "environment": "sandbox",
                 "api_key_status": "configured", "masked": "••••••", "created_at": item.created_at.isoformat()}
                for item in records]


def _get_sandbox_adapter(connection_id: str, user_id: str, store: PaperLedgerStore) -> BinanceTestnetAdapter:
    with store.sessions() as session:
        record = session.scalar(select(ExchangeCredential).where(
            ExchangeCredential.id == connection_id,
            ExchangeCredential.user_id == user_id,
        ))
        if record is None:
            raise HTTPException(404, "Exchange connection not found")
        if record.exchange != "binance":
            raise HTTPException(422, "This sandbox adapter is not supported")
        try:
            secret_bundle = json.loads(decrypt_credential(
                record.ciphertext, user_id=record.user_id, credential_id=record.id,
            ))
        except (CredentialEncryptionError, json.JSONDecodeError) as exc:
            raise HTTPException(503, "Exchange credential is unavailable") from exc
        if secret_bundle.get("environment") != "sandbox":
            raise HTTPException(422, "Only sandbox credentials are supported")
        try:
            return BinanceTestnetAdapter(secret_bundle["api_key"], secret_bundle["api_secret"])
        except (KeyError, TypeError, ExchangeError) as exc:
            raise HTTPException(503, "Exchange credential is unavailable") from exc


def _exchange_error(exc: ExchangeError) -> HTTPException:
    status = {
        "authentication_failed": 401,
        "exchange_rate_limited": 429,
        "exchange_unavailable": 502,
        "invalid_symbol": 422,
        "exchange_rejected_request": 502,
        "invalid_response": 502,
    }.get(exc.code, 502)
    return HTTPException(status, {"status": "failed", "code": exc.code})


@app.post("/api/v1/exchange-connections/{connection_id}/test", dependencies=[Depends(require_same_origin)])
def test_exchange_connection(connection_id: str, user: dict = Depends(get_current_user), store: PaperLedgerStore = Depends(get_paper_store)):
    adapter = _get_sandbox_adapter(connection_id, user["id"], store)
    try:
        result = adapter.test_connection()
    except ExchangeError as exc:
        raise _exchange_error(exc) from exc
    return {**result, "exchange": "binance", "environment": "sandbox"}


@app.get("/api/v1/exchange-connections/{connection_id}/price", dependencies=[Depends(require_same_origin)])
def exchange_market_price(connection_id: str, symbol: str, user: dict = Depends(get_current_user), store: PaperLedgerStore = Depends(get_paper_store)):
    adapter = _get_sandbox_adapter(connection_id, user["id"], store)
    try:
        return {**adapter.get_market_price(symbol), "exchange": "binance", "environment": "sandbox"}
    except ExchangeError as exc:
        raise _exchange_error(exc) from exc


@app.post("/api/v1/ai/interpret-strategy", dependencies=[Depends(require_same_origin)])
def interpret_strategy(payload: StrategyInterpretRequest, request: Request, user: dict = Depends(get_current_user), store: PaperLedgerStore = Depends(get_paper_store)):
    instruction = payload.instruction.strip()
    if not instruction:
        raise HTTPException(422, "instruction is required")
    limit_ai_interpretation(request, user["id"])
    try:
        result = get_ai_provider().interpret(instruction)
    except AIProviderNotConfigured as exc:
        raise HTTPException(503, "AI strategy interpretation is not configured") from exc
    except AIProviderUnavailable as exc:
        raise HTTPException(502, "AI strategy interpretation is temporarily unavailable") from exc
    price_filter_validator = None
    if payload.exchange_connection_id:
        adapter = _get_sandbox_adapter(payload.exchange_connection_id, user["id"], store)
        price_filter_validator = adapter.validate_price
    result = validate_interpretation(instruction, result, price_filter_validator)
    return result.model_dump(mode="json")


@app.post("/api/v1/agents", status_code=201, dependencies=[Depends(require_same_origin)])
def create_agent(payload: AgentCreate, user: dict = Depends(get_current_user), agents: AgentStore = Depends(get_agent_store)):
    return agents.create(user["id"], payload.name, payload.strategy.model_dump(mode="json"))


@app.get("/api/v1/agents")
def list_agents(user: dict = Depends(get_current_user), agents: AgentStore = Depends(get_agent_store)):
    return agents.list(user["id"])


@app.get("/api/v1/agents/{agent_id}")
def get_agent(agent: dict = Depends(verify_agent_ownership)):
    return agent


@app.post("/api/v1/agents/{agent_id}/transition", dependencies=[Depends(require_same_origin)])
def transition_agent(agent_id: str, request: TransitionRequest, user: dict = Depends(get_current_user), agents: AgentStore = Depends(get_agent_store), _owned_agent: dict = Depends(verify_agent_ownership)):
    try:
        return agents.transition(agent_id, user["id"], request.target.value, request.explicit_approval)
    except AgentNotFound as exc:
        raise HTTPException(404, "Agent not found")
    except InvalidAgentTransition as exc:
        status = 403 if "approval" in str(exc).lower() or "live execution" in str(exc).lower() else 409
        raise HTTPException(status, str(exc)) from exc


@app.post("/api/v1/agents/{agent_id}/approve", dependencies=[Depends(require_same_origin)])
def approve_agent(agent_id: str, payload: AgentApprovalRequest, user: dict = Depends(get_current_user), agents: AgentStore = Depends(get_agent_store)):
    try:
        return agents.approve_strategy(agent_id, user["id"])
    except AgentNotFound as exc:
        raise HTTPException(404, "Agent not found") from exc
    except InvalidAgentTransition as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/v1/agents/{agent_id}/paper-orders", status_code=201, dependencies=[Depends(require_same_origin)])
async def paper_order(agent_id: str, proposal: OrderProposal, idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128), store: PaperLedgerStore = Depends(get_paper_store), agent: dict = Depends(verify_agent_ownership)):
    spec = agent["strategy"]
    allocation = Decimal(str(spec["allocated_capital"]))
    proposed_order = ProposedOrder(
        agent_id=agent_id, symbol=proposal.symbol, side=proposal.side.upper(),
        order_type=proposal.order_type.upper(), quantity=proposal.quantity,
        price=proposal.reference_price, stop_loss_price=proposal.stop_loss_price,
    )
    try:
        prior = store.get_order_by_idempotency(agent["paper_account_id"], idempotency_key, proposed_order)
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if prior is not None:
        return {"id": prior["order_id"], "agent_id": agent_id, "paper_account_id": agent["paper_account_id"], "mode": "paper", "status": prior["status"], "rejection_reasons": [prior["rejection_code"]] if prior["rejection_code"] else [], "execution": prior, "order": proposal.model_dump(mode="json"), "live_order_submitted": False}
    profile = RiskProfile(
        max_capital_per_order=allocation,
        max_daily_loss=allocation * Decimal(str(spec["max_daily_loss_percent"])) / Decimal("100"),
        allowed_symbols=[spec["symbol"]],
        allow_leverage=False,
        emergency_stop=agent["state"] in {AgentState.STOPPED, AgentState.PAUSED},
    )
    paper_account = store.get_account(agent["paper_account_id"])
    if paper_account is None:
        raise HTTPException(503, "Paper account is unavailable")
    signal = AISignal(agent_id=agent_id, symbol=proposal.symbol, action=proposal.side.upper(), amount=proposal.quantity, price=proposal.reference_price, stop_loss_price=proposal.stop_loss_price, signal_id=idempotency_key)
    adapter = PersistentPaperExecutionAdapter(store, agent["paper_account_id"])
    orchestrator = TradeOrchestrator(RISK_ENGINE, adapter, AUDIT_LOG)
    try:
        execution = await orchestrator.handle_ai_signal(
            signal, profile, Decimal(paper_account["daily_loss"]), Decimal(paper_account["available_balance"]),
            authorized=agent["state"] == AgentState.PAPER_TRADING and paper_account["status"] == "ACTIVE",
            permitted_order_types={item.upper() for item in spec["allowed_order_types"]},
            max_trade_risk=allocation * Decimal(str(spec["max_risk_per_trade_percent"])) / Decimal("100"),
            data_age_seconds=proposal.data_age_seconds,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if not execution["success"] and execution["stage"] == "RISK_ENGINE_REJECTION":
        try:
            execution["execution"] = store.record_rejection(agent["paper_account_id"], proposed_order, idempotency_key, execution["error"])
        except IdempotencyConflict as exc:
            raise HTTPException(409, str(exc)) from exc
    result = {"id": execution.get("execution", {}).get("order_id", idempotency_key), "agent_id": agent_id, "paper_account_id": agent["paper_account_id"], "mode": "paper", "status": execution.get("execution", {}).get("status", "REJECTED"), "rejection_reasons": [] if execution["success"] else [execution.get("error") or execution["execution"].get("rejection_code")], "execution": execution.get("execution"), "order": proposal.model_dump(mode="json"), "live_order_submitted": False}
    return result


@app.get("/api/v1/orders")
def list_orders(user: dict = Depends(get_current_user), store: PaperLedgerStore = Depends(get_paper_store)):
    return store.list_orders(user_id=user["id"])
