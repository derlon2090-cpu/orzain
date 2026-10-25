# TRADEGEN AI

Risk-first starter for an AI-assisted trading workspace. This repository currently provides a demo dashboard and an isolated paper-mode API foundation. **Live trading is disabled.** Demo figures and quotes in the dashboard are invented illustrative values and are labeled as such; they are not fetched market/account data.

For the proposed event-driven scale-out design and phased migration, see [docs/architecture.md](docs/architecture.md). That document is a target design, not a claim that Kafka, sharding, a distributed ledger, or million-user capacity is implemented or benchmarked.

## What works in this foundation

- Responsive dark dashboard with bilingual English/Arabic account and paper-agent review flows; sample portfolio figures remain clearly illustrative.
- FastAPI service with typed strategy/order schemas, Argon2 password hashing, verified-email login, revocable database sessions in HttpOnly cookies, and database-backed agent endpoints.
- Durable agent-state transitions guarded by row locks, immutable strategy revisions, and transactional audit records; explicit approval creates a new approved revision and starts paper monitoring.
- A single serial paper-signal worker evaluates approved PRICE/VOLUME rules against closed Binance Spot Testnet candles. Every proposed entry/exit is checked by the deterministic risk engine and settled only in the double-entry paper ledger.
- Deterministic pre-trade paper order checks using `Decimal`; paper orders, account balances, positions, reservations, and ledger postings persist through SQLAlchemy.
- Per-trade risk is calculated server-side from order quantity, entry price, and the required stop-loss price; caller-provided risk estimates are not trusted.
- Adapter-based paper execution with configurable fee/slippage, simulated quote balance/inventory, UTC-day realized-loss tracking, stable idempotency replay, plus a fail-closed live adapter placeholder.
- A risk-first orchestrator that logs typed rejection codes before returning and never calls execution after a failed risk check.
- Health endpoint and local PostgreSQL/Redis services in Compose.

## Architecture

`frontend/` is a Next.js App Router application. `backend/app/main.py` contains the versioned FastAPI routes; `backend/app/risk_engine.py`, `execution.py`, `orchestrator.py`, `paper_store.py`, `agent_store.py`, and `identity.py` isolate policy, adapters, coordination, persistence, and access control. PostgreSQL stores users, sessions, paper accounts/orders/positions, double-entry ledger rows, agents, strategy revisions, audit records, evaluator state, and encrypted credential envelopes. Redis-backed atomic rate limits protect signup, login, verification, and password-reset routes; development can fall back to process-local limits. Exchange sandbox credentials are stored encrypted and API responses expose masked status only. A read-only Binance Spot Testnet adapter and a strict-schema strategy parser feed an isolated paper evaluator; the evaluator never submits exchange orders.

## Run locally

Requirements: Node.js 20+, Python 3.11+ and Docker Desktop (needed for database/Redis and the containerized TLS deployment).

```powershell
Copy-Item .env.example .env
npm install
npm run dev
```

Open http://localhost:3000. The dashboard demo view does not require secrets or external credentials.

To run the API in a second terminal:

```powershell
py -m venv backend/.venv
backend/.venv/Scripts/Activate.ps1
pip install -r backend/requirements-dev.txt
py -m uvicorn app.main:app --app-dir backend --env-file .env --reload --port 8000
```

API docs: http://localhost:8000/docs. Health: http://localhost:8000/health.

Run backend tests from the repository root with `py -m pytest -q`.

Local database and cache:

```powershell
docker compose up -d db redis
```

After PostgreSQL is healthy, apply all schema migrations, including ownership backfill, from the repository root:

```powershell
py -m alembic -c backend/alembic.ini upgrade head
```

Alembic reads `DATABASE_URL` from the root `.env`; the API command above loads that same file. The Compose PostgreSQL service initializes separate `tradegen_dev` and `tradegen_test` databases on a fresh data volume. Set `DATABASE_URL` to `tradegen_dev` for local application work and `TEST_DATABASE_URL` to `tradegen_test` for database integration tests. SQLite remains available for fast isolated unit tests. Never use the sample Compose password in production.

To exercise row locks against PostgreSQL from PowerShell, start the local service with `docker compose up -d db`, then migrate each isolated database and run the concurrency test:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://tradegen:local-only-change-me@localhost:5432/tradegen_dev"
py -m alembic -c backend/alembic.ini upgrade head
$env:TEST_DATABASE_URL = "postgresql+psycopg://tradegen:local-only-change-me@localhost:5432/tradegen_test"
$env:DATABASE_URL = $env:TEST_DATABASE_URL
py -m alembic -c backend/alembic.ini upgrade head
py -m pytest backend/tests/test_concurrency.py -q
```

The test creates and removes its own account and verifies simultaneous orders cannot overspend it. Do not point this at a shared application database. Init SQL runs only when the Postgres data volume is first created; an existing volume will not be modified automatically. If you already have this Compose volume, create each missing database explicitly (skip a command if that database already exists):

```powershell
docker compose exec -T db psql -U tradegen -d postgres -c "CREATE DATABASE tradegen_dev OWNER tradegen;"
docker compose exec -T db psql -U tradegen -d postgres -c "CREATE DATABASE tradegen_test OWNER tradegen;"
```

### Authentication and credential encryption

The API provides registration, verification, login/logout, password-reset request/completion, and current-user routes under `/api/v1/auth`. Registration requires a 12-character minimum password. Development logs and returns one-time tokens for local testing. Production sends tokens through SMTP and never returns them in API responses; configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, and any required SMTP credentials. Password-reset request responses do not disclose whether an account exists. The session cookie is `HttpOnly` and `SameSite=Strict`; `Secure` is enabled when `APP_ENV=production`. Set `PUBLIC_ORIGIN` and `CORS_ORIGINS` to the exact frontend origin(s). Agent and order routes require a session and scope results by user ownership. Historical accounts and agents are assigned to a disabled legacy owner by migration 0003 and are not exposed to new users. Migration 0003 intentionally refuses downgrade because that would discard tenant ownership data; take a database backup before upgrading.

For the exchange-secret encryption skeleton, set `MASTER_KEY` to Base64 encoding of 32 random bytes. Generate one with `py -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"`. Keep it in a secrets manager, back it up securely, and do not rotate it without a versioned re-encryption plan. The app refuses to encrypt or decrypt if the key is missing or not exactly 32 bytes.

## HTTPS deployment scaffold

The repository includes Nginx TLS 1.2/1.3 configuration, a temporary ACME HTTP challenge server, a Certbot renewal service, and container builds for the Next.js and FastAPI apps. The backend and frontend have no published host ports in this profile; PostgreSQL and Redis bind to localhost. The Nginx certificate watcher validates the renewed configuration and gracefully reloads Nginx after certificate files change.

Before deployment, set `DOMAIN`, `ACME_EMAIL`, and a strong `POSTGRES_PASSWORD` in `.env`, point the domain's DNS A/AAAA records at the server, and allow inbound TCP 80/443. The example domain is a placeholder and cannot obtain a certificate. In the issuance command below, replace the example email and domain with the same real values set in `.env`:

```powershell
docker compose --profile tls-bootstrap up -d nginx-bootstrap
  docker compose --profile tls run --rm --entrypoint certbot certbot certonly --webroot --webroot-path=/var/www/certbot --email admin@yourdomain.com --agree-tos --no-eff-email -d yourdomain.com
docker compose --profile tls-bootstrap stop nginx-bootstrap
docker compose --profile tls up -d --build
```

Certbot checks for renewal every 12 hours; Nginx watches the mounted certificate files and reloads after a successful renewal. HTTP serves only ACME challenges and redirects other traffic to HTTPS. HSTS is enabled for the configured host only. `includeSubDomains` and `preload` are deliberately omitted until every covered subdomain is confirmed HTTPS-only. The Next.js app emits a per-response nonce-based Content Security Policy; Nginx supplies transport and other browser-security headers.

No certificate has been issued or domain verified in this workspace. Docker and a public DNS domain controlled by the operator are required to run this setup. No SSL Labs grade or production security certification is claimed. Do not expose the demo API publicly until SMTP verification is configured, rate limits and operational controls are in place, and the remaining production review is complete.

## API examples

The strategy schema requires explicit conditions, stop loss, limits, and paper-only execution. The web flow interprets rules, shows the structured result, collects the user's explicit capital and risk limits, creates a draft, moves it to `AWAITING_APPROVAL`, and offers a separate approval action. `POST /api/v1/agents/{id}/approve` creates an incremented immutable `APPROVED` revision and changes state to `PAPER_TRADING`. The evaluator supports PRICE/VOLUME and deterministic Decimal RSI, MACD histogram, SMA, and EMA rules from closed candles; indicator periods must be stated explicitly in both the strategy and interpretation input. `ACTIVE` remains disabled. Manual paper orders require an `Idempotency-Key` header and `stop_loss_price`; orders are never sent to an exchange. Risk rejections have stable error codes and are persisted as rejected order records before returning. A repeated idempotency key returns the original result; reuse with a different payload returns HTTP 409.

The paper evaluator is a separate serial process (`python -m scripts.run_paper_evaluator` from `backend/`). It polls the Binance Spot Testnet adapter for closed candles, evaluates approved rules, calculates an entry size within the explicit allocation/risk limits, and sends each proposal through `DeterministicRiskEngine` and `PaperLedgerStore`. Transient per-agent errors are recorded as `ERROR`; the worker continues to other agents and uses bounded exponential retry with jitter between failed cycles. It only simulates fills locally; it is not exchange order execution or a realistic order-book simulator. Docker Compose starts one evaluator with the `tls` profile. Configure `EVALUATOR_INTERVAL_SECONDS` (2–300 seconds; default 10). Connect a Binance Spot Testnet credential before approving agents intended for monitoring.

Paper-order creation first commits a unique command record and cash reservation. Settlement is a second atomic transaction that writes balanced ledger entries and releases the account fence. If the process stops between these transactions, the account remains `RECONCILIATION_REQUIRED`; retrying the same key returns the pending command without resubmitting it. After reviewing the pending paper command, resolve it with:

```powershell
cd backend
py -m scripts.reconcile_paper_order <order-id>
py -m scripts.verify_paper_ledger <account-id>
```

The reconciliation script deterministically settles a **paper** order. It is not an exchange reconciler and must not be reused for live execution.

```json
{
  "name": "BTC rule set",
  "strategy": {
    "symbol": "BTC/USDT", "market_type": "spot", "allocated_capital": "100",
    "currency": "USDT", "leverage_allowed": false,
    "max_risk_per_trade_percent": "1", "max_daily_loss_percent": "3",
    "entry_conditions": [{"kind":"price_cross","indicator":"PRICE","operator":"crosses_above","value":"65000","timeframe":"5m","symbol":"BTCUSDT"}],
    "exit_conditions": [{"kind":"price_cross","indicator":"PRICE","operator":"crosses_below","value":"64000","timeframe":"5m","symbol":"BTCUSDT"}],
    "stop_loss_percent": "2",
    "stop_loss_required": true, "max_open_positions": 1,
    "allowed_order_types": ["market"], "execution_mode": "paper"
  }
}
```

## Configuration and security status

`.env.example` contains placeholders only. The API has Argon2-backed accounts, verified-email checks, revocable database sessions, and account/agent ownership filters. Production signup requires SMTP configuration; one-time tokens are never returned by production endpoints. `POST` and `GET /api/v1/exchange-connections` support sandbox credentials using AES-256-GCM; reads return masked status and never decrypt secrets. `POST /api/v1/exchange-connections/{id}/test` performs an authenticated read-only account request against the hard-coded Binance Spot Testnet host; `GET /api/v1/exchange-connections/{id}/price?symbol=BTCUSDT` reads the testnet price. Price and quantity validation use testnet `PRICE_FILTER` and `LOT_SIZE` rules. This adapter does not submit orders. `POST /api/v1/ai/interpret-strategy` requires an authenticated session and an `AI_PROVIDER=openai` configuration with `AI_API_KEY`; it returns only structured entry/exit conditions. Include an owned `exchange_connection_id` to validate explicit price thresholds against that sandbox's tick size. Vague/invented thresholds and missing symbols/timeframes require clarification; the parser has no fields for capital, leverage, sizing, or execution. Production rate limiting requires a reachable `REDIS_URL` and fails closed if Redis is unavailable. The current service remains a local demonstration and must not be used with real funds. CORS defaults to localhost and can be set with `CORS_ORIGINS`. Paper mode simulates immediate fills and has no order book; it is not a realistic market simulator.

## Not implemented yet

MFA, roles/RBAC, credential rotation/deletion, live exchange connectivity/order placement, production-grade AI safety evaluations, realistic market simulation, distributed/partitioned worker coordination, backtesting, notifications, billing, admin controls, public/legal routes, production monitoring, backups, and recovery processes. The evaluator is currently a single polling process and has not been load-tested for large user counts. Agent lifecycle audit records are persisted; the separate orchestrator diagnostic rejection list remains in memory. No mainnet connection or live order placement is supported. Exchange data is sandbox-only.

## Before production

Independently review the new authentication and authorization before exposing APIs; add MFA and abuse monitoring; validate deployment secrets and SMTP delivery; review the sandbox adapter and credential lifecycle; obtain jurisdiction-specific legal advice; complete threat modeling, dependency/container scanning, operational monitoring, backup/restore drills, and comprehensive integration/E2E tests. Keep live trading feature-disabled until each safeguard and required configuration is independently verified.
