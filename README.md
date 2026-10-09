# Shopee Seller Insights

[![CI](https://github.com/GustavoPFARIA/shopee-seller-insights/actions/workflows/ci.yml/badge.svg)](https://github.com/GustavoPFARIA/shopee-seller-insights/actions/workflows/ci.yml)
[![CodeQL](https://github.com/GustavoPFARIA/shopee-seller-insights/actions/workflows/codeql.yml/badge.svg)](https://github.com/GustavoPFARIA/shopee-seller-insights/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/GustavoPFARIA/shopee-seller-insights)](https://github.com/GustavoPFARIA/shopee-seller-insights/releases)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![React](https://img.shields.io/badge/React-19-20232A?logo=react)
![Claude](https://img.shields.io/badge/LLM-Claude-D97757)
![Coverage](https://img.shields.io/badge/coverage-99%25-brightgreen)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

![Demo: dashboard with real margins and alerts, product filters (losing money, high returns), editing a product's cost and switching shops](docs/demo.gif)

<sub>Real margin per product, ABC curve and alerts → filter products losing money or with high returns → edit cost and stock → switch between shops.</sub>

Sales analytics for small Shopee sellers. Connect a shop through the **Shopee Open
Platform API** or upload the order report from Seller Centre. The app then shows revenue,
**real margin per product** (after Shopee commission, service and transaction fees,
seller-paid shipping, coupons and product cost), the ABC curve, period comparison and
alerts you can act on.

### Highlights for reviewers

- **Grounded LLM feature, not a chatbot wrapper.** The weekly summary is written by
  Claude from a small JSON of pre-computed aggregates. Every number comes from SQL, the
  model only explains them, and no order or customer data ever reaches the model
  (asserted by a test). The feature turns itself off without an API key.
- **Data pipeline you can trust.** Messy marketplace exports and a signed third-party
  API (OAuth, HMAC, webhooks) become one clean, idempotent dataset that the metrics,
  the alerts and the LLM all read from.
- **Production engineering.** Multi-tenant isolation, LGPD-minded PII handling, a
  background worker, 216 backend + 16 frontend tests, a 52-check end-to-end smoke test
  and a performance budget in CI.

## Table of contents

- [The problem](#the-problem)
- [Features](#features)
- [Tech stack](#tech-stack)
- [Getting started](#getting-started)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [Database schema](#database-schema)
- [Shopee Open Platform integration](#shopee-open-platform-integration)
- [Security & data protection](#security--data-protection)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

## The problem

Shopee Seller Centre shows gross sales. A seller who wants to know *which products
actually make money* has to export spreadsheets and redo the math by hand:

- Fees are charged per order, not per product.
- Product cost is not in the export.
- Cancelled and unpaid orders are mixed in with real sales.

This app does that math the same way every time. It keeps history across uploads and
syncs without duplicating orders, and it flags what needs attention.

## Features

- **Shopee Open Platform integration (API v2)**:
  - The shop owner authorizes the app with OAuth.
  - A background worker imports orders on a schedule. A manual "Sync now" button
    does the same on demand.
  - **Push notifications (webhook)**: when Shopee notifies a new or changed order, a
    sync of that shop is queued at once. Only signed notifications are accepted.
  - Exact fees come from the escrow statement. Stock is synced by SKU, variations included.
- **Upload** of CSV or XLSX order exports, with Brazilian Seller Centre headers in
  Portuguese or the English export headers:
  - Every row is validated with Pydantic, and errors point to the line and column.
  - **Idempotent**: re-uploading the same or an overlapping report never duplicates
    orders. It only updates their status.
- **Metrics**: revenue, orders, units, average ticket, net margin, daily revenue, and
  comparison with the previous period of the same length.
- **Real margin per product**:
  - Order-level fees are split across items in proportion to their value, exact to the
    cent.
  - `margin = revenue − commission − service fee − seller shipping − coupons − unit cost × units`.
  - A product with no cost shows "not set" instead of a misleading number.
- **ABC curve** (Pareto): A up to 80% of cumulative revenue, B up to 95%, C for the rest.
- **Returns per product**: returned and cancelled units, and the return rate.
- **Alerts**: low stock, stalled products (no sales in N days while stock is on hand),
  margin below a threshold, and a return rate above a threshold. Each shop sets its own
  thresholds in **Settings**.
- **Cost and stock spreadsheet**: download the catalogue, fill in cost and stock in
  Excel or Sheets, and upload it back. Empty cells keep the current value.
- **Teams**: a shop can have several users with **owner / manager / viewer** roles,
  invited through one-time links (e-mailed when SMTP is configured).
- **Several shops per account**: one login can manage several shops (for example one per
  country or brand), with a different role in each. A shop switcher sits in the sidebar.
- **Weekly e-mail summary** (optional, needs SMTP): owners and managers get last week's
  numbers and alerts every Monday. A preview can be sent from Settings.
- **CSV export** of product metrics, protected against spreadsheet formula injection.
- **Optional AI weekly summary** with Claude (`claude-haiku-4-5`). It is enabled only
  when `ANTHROPIC_API_KEY` is set, and the model gets **aggregates only**.
- **Demo data**:
  - A deterministic seed generates a realistic, fully fake Seller Centre export and
    loads it **through the same importer** that user uploads go through.
  - The fake data has Zipf-like product popularity, weekly seasonality, the real fee
    structure and a realistic mix of order statuses.

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, Uvicorn |
| Data | PostgreSQL 16, SQLAlchemy 2.0, Alembic, pandas, openpyxl |
| Frontend | React 19, TypeScript, Vite, Recharts |
| Integrations | Shopee Open Platform v2 (OAuth, HMAC, webhooks) with httpx and cryptography (Fernet), SMTP |
| AI | Claude API (`claude-haiku-4-5`), optional, grounded on SQL aggregates |
| Infrastructure | Docker Compose, nginx (unprivileged), background worker container |
| Quality | pytest, ruff, mypy `--strict`, Vitest + Testing Library, oxlint, end-to-end smoke test (Python standard library) |
| CI and security | GitHub Actions, CodeQL, Dependabot, gitleaks, pip-audit, npm audit |

## Getting started

Requirements: Docker with Compose v2 ([Docker Desktop](https://www.docker.com/products/docker-desktop/) on Windows and macOS).

**One click:** download the project ([ZIP](https://github.com/GustavoPFARIA/shopee-seller-insights/archive/refs/heads/main.zip)
or `git clone`), open Docker Desktop, then double-click **`start.bat`** on Windows or run
**`./start.sh`** on macOS/Linux. The script checks that Docker is running, starts the
stack and opens the browser when the app is ready.

Or by hand:

```bash
git clone https://github.com/GustavoPFARIA/shopee-seller-insights.git
cd shopee-seller-insights
docker compose up --build
```

Open **http://localhost:8080** and sign in:

| E-mail | Password | Role |
|---|---|---|
| `demo@shopee-insights.dev` | `DemoPassword123!` | owner |
| `viewer@shopee-insights.dev` | `DemoPassword123!` | viewer (read-only) |
| `other@shopee-insights.dev` | `DemoPassword123!` | owner of a different shop (isolation check) |

The demo owner is also a **manager of that other shop**, to show the shop switcher.

On first start the API applies the migrations and loads ~120 days of fake orders.
Swagger UI is at http://localhost:8080/api/docs. To try an upload, use
[`docs/sample-orders.csv`](docs/sample-orders.csv), which is fake data in the Seller
Centre format.

For anything beyond a local demo:

```bash
cp .env.example .env   # then fill in real secrets (and Shopee credentials, if any)
```

To stop the stack, run `docker compose down`. Adding `-v` also deletes the database
volume, and the demo data is loaded again on the next start.

### Using it with your own shop

1. On the sign-in page choose **Create account** and name your shop (keep the demo shop separate).
2. In Shopee Seller Centre open **My Orders → Export**, pick a period and download the report.
3. In the app open **Upload report** and drop the file. Re-uploading an overlapping period never duplicates orders.
4. Open **Products → Download spreadsheet**, fill in `unit_cost` (and `stock_quantity` if you track stock), then **Import spreadsheet**. Margins need the cost.
5. The **Dashboard** now shows real margin per product, the ABC curve and alerts. Adjust alert thresholds in **Settings**.

To sync automatically instead of uploading, connect the shop through the Shopee Open Platform (see [below](#shopee-open-platform-integration)).

### Configuration

All settings are environment variables, read by `pydantic-settings` and validated at
startup. Invalid values stop the API with a clear error.

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | `production` refuses dev-only secrets and `COOKIE_SECURE=false`, and hides Swagger. |
| `DATABASE_URL` | – | Connection for the API and worker, using the least-privilege `ssi_app` role. |
| `MIGRATION_DATABASE_URL` | – | Connection for Alembic, using the schema owner role. |
| `JWT_SECRET` | – (required, ≥ 32 chars) | Signs access tokens. |
| `PII_HASH_SECRET` | – (required, ≥ 32 chars) | Key for pseudonymizing buyer usernames. Changing it breaks the link with buyers already stored. |
| `ACCESS_TOKEN_MINUTES` / `REFRESH_TOKEN_DAYS` | `15` / `7` | Session lifetimes. |
| `COOKIE_SECURE` | `true` | Send the refresh cookie over HTTPS only. Set `false` only for a local http demo. |
| `REPORT_TIMEZONE` | `America/Sao_Paulo` | Time zone used to group sales by calendar day. |
| `CORS_ORIGINS` | `["http://localhost:8080", "http://localhost:5173"]` | Browser origins allowed to call the API directly. |
| `MAX_UPLOAD_MB` / `MAX_UPLOAD_ROWS` | `5` / `50000` | Upload limits. |
| `LOGIN_RATE_LIMIT` / `LOGIN_RATE_WINDOW_SECONDS` | `5` / `60` | Login, register and accept-invite attempts per client IP. |
| `UPLOAD_RATE_LIMIT` / `UPLOAD_RATE_WINDOW_SECONDS` | `10` / `3600` | Limit per client IP, applied separately to uploads, AI summaries, Shopee syncs and invitations. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | The only proxy address whose `X-Forwarded-For` header is trusted. Compose sets it to nginx's address. |
| `SEED_DEMO_DATA` | `true` (compose) | Load the fake demo shop on first start. |
| `ANTHROPIC_API_KEY` | empty | Enables the AI weekly summary. |
| `SHOPEE_PARTNER_ID`, `SHOPEE_PARTNER_KEY`, `TOKEN_ENCRYPTION_KEY` | empty | Enable the Shopee integration. All three are required. |
| `SHOPEE_API_HOST` | Shopee test environment | Production: `https://partner.shopeemobile.com`. |
| `SHOPEE_REDIRECT_URL` | `http://localhost:8080/api/shopee/callback` | Must match the URL registered on the Shopee console. |
| `SHOPEE_SYNC_INTERVAL_MINUTES` / `SHOPEE_BACKFILL_DAYS` | `30` / `90` | Scheduled sync interval and first-sync history. |
| `SHOPEE_AUTH_HOST` | same as `SHOPEE_API_HOST` | Host of the browser authorization page (only the demo mode changes it). |
| `SHOPEE_PUSH_URL` / `SHOPEE_PUSH_KEY` | empty | Enable push notifications. The URL must match the one registered on Shopee exactly. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | empty / `587` | Enable e-mail (invitations and the weekly summary). |
| `SMTP_SECURITY` | `starttls` | `starttls`, `ssl` or `none` (`none` is refused in production). |
| `APP_BASE_URL` | `http://localhost:8080` | Public address used in e-mailed links. |
| `WEB_PORT` | `8080` | Host port of the web app (compose only). |

### Local development without Docker

```bash
docker run -d --name ssi-db -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=shopee_insights_test -p 5433:5432 postgres:16-alpine

cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5433/shopee_insights_test
export MIGRATION_DATABASE_URL=$DATABASE_URL COOKIE_SECURE=false
export JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
export PII_HASH_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
alembic upgrade head && python -m app.seed
uvicorn app.main:app --reload

cd ../frontend && npm ci && npm run dev   # http://localhost:5173 (proxies /api to :8000)
```

## Screenshots

| Dashboard | Products (cost, stock, spreadsheet) |
|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Products](docs/screenshots/products.png) |

| Integrations (Shopee sync and push) | Team and invitations |
|---|---|
| ![Integrations](docs/screenshots/integrations.png) | ![Team](docs/screenshots/team.png) |

| Upload | Settings |
|---|---|
| ![Upload](docs/screenshots/upload.png) | ![Settings](docs/screenshots/settings.png) |

| Editing a product | Mobile, dark mode |
|---|---|
| ![Edit product](docs/screenshots/product-edit.png) | ![Mobile](docs/screenshots/dashboard-mobile-dark.png) |

## Architecture

```mermaid
flowchart LR
    B[Browser] -->|HTTP :8080| W["web<br/>nginx-unprivileged<br/>static React build + CSP"]
    W -->|/api proxy| A["api<br/>FastAPI + Pydantic v2<br/>SQLAlchemy 2.0"]
    A -->|DML-only role ssi_app| D[("db<br/>PostgreSQL 16<br/>no published port")]
    K["worker<br/>same image as api<br/>scheduled Shopee sync"] -->|DML-only role| D
    M["alembic upgrade head<br/>(owner role ssi_owner)"] --> D
    A -->|OAuth, Sync now| S["Shopee Open Platform v2<br/>signed HMAC-SHA256"]
    K -->|orders, escrow, stock| S
    A -.->|aggregates only, optional| C["Claude API<br/>claude-haiku-4-5"]
    subgraph docker compose
      W
      A
      K
      D
      M
    end
```

```
backend/
  app/
    api/            FastAPI routers (auth, uploads, products, metrics, alerts,
                    summary, members, shopee)
    services/       importer, catalog_import, metrics, alerts, sessions, members, ai_summary
    integrations/   shopee_client (signed v2 client), shopee_sync (OAuth, orders, stock)
    worker.py       scheduled Shopee sync loop
    models.py       SQLAlchemy models      schemas.py   Pydantic API schemas
    config.py       pydantic-settings      security.py  Argon2, JWT, HMAC pseudonymization
    ratelimit.py    PostgreSQL rate limiter  crypto.py  Fernet encryption of third-party tokens
    seed.py         fake demo data generator
  alembic/          migrations (the only way the schema changes)
  tests/            pytest suite on real PostgreSQL, plus a fake Shopee that checks signatures
  benchmarks/       performance benchmark on a large synthetic dataset
frontend/
  src/              React app (api.ts typed client, pages/, components/)
  nginx.conf        static serving, /api proxy, security headers
db/init/            creates the least-privilege application role
scripts/
  verify.sh         one command for every check (static, test, security, e2e, perf)
  smoke_test.py     end-to-end checks against a running stack, through nginx
```

## Database schema

```mermaid
erDiagram
    sellers ||--o{ users : has
    sellers ||--o{ invitations : sends
    sellers ||--o{ products : sells
    sellers ||--o{ uploads : receives
    sellers ||--o{ orders : owns
    sellers ||--o| shopee_connections : "is linked to"
    sellers ||--o{ sync_runs : logs
    users ||--o{ refresh_tokens : "signs in with"
    uploads ||--o{ orders : "first imported by"
    orders ||--|{ order_items : contains
    products ||--o{ order_items : "sold as"

    sellers {
        int id PK
        string name
        string shop_code UK
    }
    users {
        int id PK
        int seller_id FK
        string email UK
        string password_hash "Argon2id"
        string role "owner, manager, viewer"
    }
    invitations {
        int id PK
        int seller_id FK
        string email
        string role
        string token_hash UK "SHA-256"
        timestamptz expires_at
    }
    refresh_tokens {
        int id PK
        int user_id FK
        string family_id
        string token_hash UK "SHA-256"
        timestamptz expires_at
        timestamptz revoked_at
    }
    products {
        int id PK
        int seller_id FK
        string sku
        string name
        numeric unit_cost "NULL = not informed"
        int stock_quantity "NULL = not tracked"
        int low_stock_threshold
    }
    uploads {
        int id PK
        int seller_id FK
        int user_id FK
        string file_sha256
        int orders_created
    }
    orders {
        int id PK
        int seller_id FK
        string order_sn
        string status
        timestamptz ordered_at
        string buyer_hash "HMAC, never raw"
        string source "file or shopee_api"
        bool fees_final "fees from final escrow"
    }
    order_items {
        int id PK
        int order_id FK
        int product_id FK
        int quantity
        numeric unit_price
        numeric commission_fee
        numeric service_fee
        numeric seller_shipping_fee
        numeric seller_voucher
    }
    shopee_connections {
        int id PK
        int seller_id FK "unique"
        bigint shop_id UK
        text access_token_enc "Fernet"
        text refresh_token_enc "Fernet"
        timestamptz orders_synced_until
    }
    sync_runs {
        int id PK
        int seller_id FK
        string trigger "manual or scheduled"
        string status
        int orders_created
        int products_stock_updated
    }
```

Two support tables are not drawn above. `oauth_states` holds single-use OAuth CSRF
states, stored hashed. `rate_limit_hits` holds the rate-limit counters, keyed by HMAC.

- **Unique keys**:
  - `orders(seller_id, order_sn)` is the idempotency key for uploads and syncs.
  - `products(seller_id, sku)`, `order_items(order_id, product_id)`, `users(email)`,
    `shopee_connections(shop_id)`.
- **CHECK constraints**: no negative prices, costs, fees or stock; `quantity > 0`.
  Roles, statuses and sources are limited to their allowed values.
- **Money** is `NUMERIC(12,2)` / `Decimal` end to end, never float. The Shopee API
  returns floats, which go through `str()` before becoming `Decimal`.
- **Timestamps** are `timestamptz` in UTC. Days are grouped in the seller's time zone
  (`REPORT_TIMEZONE`, default `America/Sao_Paulo`) inside PostgreSQL.

## Shopee Open Platform integration

1. **Credentials.** Create an app at [open.shopee.com](https://open.shopee.com) and
   register the redirect URL `https://<your-host>/api/shopee/callback`. Set these in
   `.env`:
   - `SHOPEE_PARTNER_ID`, `SHOPEE_PARTNER_KEY`, `SHOPEE_API_HOST`.
   - `SHOPEE_REDIRECT_URL`, matching the URL you registered.
   - `TOKEN_ENCRYPTION_KEY`, a Fernet key. The command to generate one is in `.env.example`.
2. **Authorization.** A shop **owner** clicks *Connect Shopee shop* and authorizes on
   Shopee. Shopee then sends the browser back to `/api/shopee/callback`.
   - The callback checks a single-use `state` (CSRF). The state is also **bound to the
     browser** that started the flow through an HttpOnly cookie, so an attacker cannot
     send their authorization link to a victim to get the victim's shop linked to the
     attacker's account.
   - It then exchanges the code for tokens and stores the tokens **encrypted**.
   - A Shopee shop can be linked to only one account.
3. **Order sync.**
   - Orders are listed by `update_time` in 15-day windows, which is the API limit. The
     first sync backfills 90 days.
   - Details come from `get_order_detail` in batches of 50.
   - Exact fees come from `get_escrow_detail`: commission, service + transaction fee,
     seller voucher, and shipping net of the Shopee rebate and the buyer-paid part.
   - Orders still in progress are re-checked until the escrow statement is final. This
     includes completed orders whose escrow call failed, which would otherwise fall
     behind the high-water mark.
   - For Brazil, `net_commission_fee` and `net_service_fee` are used when present.
   - **The recipient address is never requested.** The buyer username is pseudonymized,
     as with uploads.
4. **Stock sync.** `get_item_list`, then `get_item_base_info`, then `get_model_list`.
   Stock is matched to the catalogue by SKU.
5. **Scheduling.**
   - The `worker` container syncs every `SHOPEE_SYNC_INTERVAL_MINUTES` minutes.
   - **"Sync now" only queues a run** (HTTP 202). The worker picks it up within
     seconds, so a first backfill with thousands of API calls never runs inside an HTTP
     request. The UI polls until the run finishes.
   - Queued runs are claimed with `FOR UPDATE SKIP LOCKED`, and a PostgreSQL advisory
     lock prevents two syncs of the same shop at once.
   - Expired access tokens are refreshed automatically. When the 30-day refresh token
     expires or Shopee rejects it (the seller revoked access), the run is recorded as
     `reauthorization_required` and owners see a **Reconnect** button.
   - Every run is listed on the Integrations page.
6. **Push notifications.** Register `https://<your-host>/api/shopee/push` as the push
   URL on the Shopee console and set `SHOPEE_PUSH_URL` and `SHOPEE_PUSH_KEY`.
   - The signature is `HMAC-SHA256(push key, push URL + "|" + raw body)` in the
     `Authorization` header, compared in constant time. Unsigned requests get 401.
   - The body is never trusted for data: an order event only queues a sync of that
     shop, which reads everything through the signed API.

### Try it without a Shopee account (demo mode)

`docker-compose.shopee-demo.yml` adds a **fake Shopee** server
([`devtools/fake_shopee_server.py`](backend/devtools/fake_shopee_server.py)) with a
consent page, seeded orders and a button that creates a new order and sends a signed
push notification:

```bash
docker compose -f docker-compose.yml -f docker-compose.shopee-demo.yml up --build
```

Go to **Integrations → Connect Shopee shop**, approve on the fake consent page, then
open http://localhost:9555 and click *Create a new order*: the order appears after the
push-triggered sync. The demo uses a public, demo-only encryption key that production
refuses to start with.

Without these credentials the integration stays off, the worker sits idle and manual
uploads keep working.

## Security & data protection

| Concern | Decision |
|---|---|
| Authentication | **Argon2id** password hashes. Short-lived JWT access token (15 min), kept **in memory only** in the browser. Login does a dummy hash for unknown e-mails, so timing does not reveal which e-mails exist. |
| Sessions | The refresh token is opaque, sits in an **HttpOnly, SameSite=Strict** cookie scoped to `/api/auth`, and is stored only as SHA-256. It **rotates on every use**. Reusing a token that was already rotated revokes the whole session family, since that signals theft. A 30-second grace window lets two tabs refresh at the same moment. `/refresh` and `/logout` also require an `X-Requested-With` header against CSRF. Production refuses `COOKIE_SECURE=false`. |
| Authorization | Each request checks the user's role, read from the database (owner, manager, viewer), so a role change or removal applies at once. Removing a member also revokes their sessions. Membership changes lock the shop row, so a shop can never lose its last owner, even when two owners act at the same time. Invitations do not reveal which e-mails already have an account. |
| Tenant isolation | Every query filters by the authenticated user's `seller_id`, plus a second filter on joined tables. A foreign id answers 404, like a missing one. Covered by tests on every endpoint. |
| Secrets | Read only from the environment via `pydantic-settings`. `.env` is git-ignored. The API **refuses to start** with the compose `dev-only` defaults when `APP_ENV=production`. gitleaks scans the full git history in CI. |
| Third-party tokens | Shopee access and refresh tokens are encrypted with Fernet (`TOKEN_ENCRYPTION_KEY`). HTTP client loggers are capped at WARNING, because Shopee puts `access_token` in the query string. A regression test checks that no token reaches the logs. |
| OAuth | The `state` is single-use, expires in 10 minutes, is stored as SHA-256 and is **bound to the initiating browser** with an HttpOnly cookie. The callback redirects only with fixed error codes. |
| Invitations | One-time tokens, valid 72 hours, stored as SHA-256. The link carries the token in the **URL fragment** (`/#invite=…`), so it never reaches server logs or `Referer` headers. |
| Uploads | Extension allow-list, max size and max rows, UTF-8 check, XLSX magic bytes and a zip-bomb guard. Imports are all-or-nothing. |
| CSV/formula injection | Text cells starting with `= + - @ \t \r` are rejected on import and on Shopee sync. Exports prefix such cells with `'`. |
| SQL injection | SQLAlchemy ORM/Core with bound parameters only. |
| Rate limiting | Counters live in PostgreSQL, so the limits hold across API replicas. Keys are HMACs, so no raw IPs are stored. nginx **overwrites** `X-Forwarded-For`, and the API trusts that header only from nginx's fixed IP. |
| HTTP headers | Strict CSP (`script-src 'self'`, `frame-ancestors 'none'`), `nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, COOP. Swagger is disabled in production. |
| Personal data (LGPD) | Recipient name, phone and address are dropped on import and never requested from the API. Buyer usernames become HMAC-SHA256 values. Errors report line, column and reason, never cell values. |
| AI | Only an aggregate JSON goes to the model, and it is returned to the user as `facts` for transparency. |
| Infrastructure | Migrations run as the schema owner. The API and worker connect as `ssi_app` (DML only). The DB and API publish no host ports; only nginx does. All containers run as non-root users. |
| Supply chain | `pip-audit`, `npm audit` and gitleaks in CI; pinned Python dependencies and an npm lockfile. |

See [SECURITY.md](SECURITY.md) for known limitations and how to report a vulnerability.

## Verification

One command checks the whole project. CI runs the same script on every push.

```bash
scripts/verify.sh            # everything (about 5 minutes)
scripts/verify.sh static     # lint, format, strict typing, frontend lint + build
scripts/verify.sh test       # backend tests on a real PostgreSQL
scripts/verify.sh security   # pip-audit, npm audit, gitleaks, no committed secrets
scripts/verify.sh e2e        # fresh docker compose stack + 52-check smoke test
scripts/verify.sh perf       # benchmark on a large synthetic dataset
```

The script starts a temporary PostgreSQL container when `TEST_DATABASE_URL` is not
set. The e2e stage runs a separate compose project (`ssi-verify`) on port 18080 and
deletes its volumes at the end, so it never touches your demo data.

### What each stage proves

| Stage | Checks |
|---|---|
| `static` | `ruff` lint and format, `mypy --strict` on app, tests and benchmarks, frontend lint, 16 frontend unit tests (Vitest + Testing Library), TypeScript type check and production build. |
| `test` | 216 backend tests with ≥ 85% coverage (currently ~99%), on a real PostgreSQL 16 with the real migrations. Also checks that the migrations match the models (`alembic check`) and that every migration can be rolled back and applied again. |
| `security` | Known-vulnerability audit of Python and npm dependencies; a gitleaks secret scan of the full git history; no `.env`, key or certificate files tracked by git. |
| `e2e` | Builds the images and starts the stack from scratch, then runs [`scripts/smoke_test.py`](scripts/smoke_test.py) through nginx, the way a user (and an attacker) would. It also checks that the worker is running and that no token or password appears in the logs. |
| `perf` | Loads a large synthetic dataset, measures p50/p95 latency of every read endpoint and the time of a maximum-size upload, prints the PostgreSQL query plan, and fails if a p95 exceeds the budget. |

The backend tests cover:

- idempotent re-upload and Shopee sync, including two uploads racing each other
- fee allocation and margin math, checked by hand
- ABC thresholds and local-day boundaries in the seller's time zone
- cross-seller isolation on every endpoint
- authentication failures, refresh-token rotation and reuse detection
- role permissions and the last-owner rule
- rate limits
- oversized, disguised and formula-injection uploads
- personal data never stored
- the AI payload carrying aggregates only
- no Shopee token ever written to the logs

The Shopee tests run against a **fake Shopee Open Platform**
([`devtools/fake_shopee.py`](backend/devtools/fake_shopee.py)). Like the real API, it checks
every HMAC signature and timestamp, enforces the 15-day window, paginates and can inject
failures such as 429s and expired tokens.

The smoke test groups its 52 checks into nine areas:

1. **HTTP security headers:** CSP, `nosniff`, `Referrer-Policy`, and the nginx version
   hidden.
2. **Network exposure:** the database and API ports are not published on the host.
3. **Sessions:**
   - wrong passwords are rejected and the refresh cookie flags are correct;
   - refresh without the CSRF header is rejected;
   - refresh after logout fails.
4. **Metrics:** the demo data loads and the metrics, ABC curve and alerts all respond.
5. **Uploads:**
   - the sample export imports, and importing it twice creates no new orders;
   - formula injection, a disguised binary and a file over 5 MB are all rejected.
6. **Isolation:** another shop can neither see nor edit the demo shop's data, and a
   viewer cannot upload or invite.
7. **Shops and settings:** the shop switcher (`X-Shop-Id`) works for members and answers
   403 for everyone else; settings are validated and read-only for viewers; product
   metrics include returns.
8. **Optional integrations:** the AI summary and Shopee endpoints answer even without
   credentials, and an unsigned push notification is rejected.
9. **Brute force:** login is rate limited even when `X-Forwarded-For` is spoofed.

### Performance

Measured with `scripts/verify.sh perf` and `PERF_ORDERS=200000`. The database held
400,000 orders and 510,000 items across 21 shops; the largest shop has 200,000 orders
and 500 products. Latencies are in-process (application + PostgreSQL) on a laptop-class
machine.

| Operation | p95 |
|---|---|
| Overview with period comparison, 30 days / 365 days | 126 ms / 374 ms |
| Real margin per product, 365 days | 116 ms |
| ABC curve, 365 days | 133 ms |
| Daily revenue, 365 days | 224 ms |
| Alerts | 157 ms |
| Upload of the largest allowed file (50,000 rows, 3.8 MB) | 11 s |
| Re-upload of the same file (nothing new to write) | 1.8 s |

Design choices behind these numbers:

- **Period filters use the index.** A period is converted once into a UTC range (local
  midnight to local midnight), so PostgreSQL uses the `(seller_id, ordered_at)` index
  instead of converting the time zone of every row.
- **One query for the headline numbers.** Revenue, orders, units and net margin come
  from a single aggregate query per period.
- **Set-based imports.** Each chunk of 2,000 orders takes one `SELECT` for existing
  orders, one bulk status `UPDATE`, one `INSERT … ON CONFLICT DO NOTHING` and one bulk
  item insert. The first version wrote one order per round trip and took 167 s for the
  same file.

CI runs the benchmark with 50,000 orders and a 1.5 s budget, because shared runners
are slower and noisier.

## Troubleshooting

| Problem | Fix |
|---|---|
| Docker Desktop says **"WSL not installed"** (Windows) | Open PowerShell as administrator, run `wsl --install`, restart the computer, then open Docker Desktop again. |
| `wsl --install` says virtualization is disabled | Enable virtualization (Intel VT-x / AMD-V) in the computer's BIOS or UEFI settings. |
| `start.bat` says Docker is not running | Open Docker Desktop and wait for **Engine running** in the bottom-left corner. |
| Windows shows "Windows protected your PC" for `start.bat` | Click **More info → Run anyway**. It appears for any downloaded script. |
| `port is already allocated` | Another program uses port 8080. Run with another port: `WEB_PORT=8081 docker compose up` (PowerShell: `$env:WEB_PORT=8081; docker compose up`). |
| The first start is slow | It builds the images, 5 to 10 minutes. Later starts take seconds. |
| Odd file errors when the project is in OneDrive | Move the folder outside OneDrive (for example `C:\projects\shopee-seller-insights`). |
| An upload is rejected | The message lists the row and column. Exports must be `.csv` or `.xlsx` from Seller Centre, up to 5 MB and 50,000 rows. |
| Start over with fresh demo data | `docker compose down -v`, then start again. |

## Roadmap

Done:

- Shopee Open Platform integration: OAuth, order, escrow and stock sync, scheduled
  worker, push notifications, and a fake-Shopee demo mode
- Spreadsheet import of product cost and stock
- Returns per product and a high-return alert
- Per-shop alert thresholds
- Several shops per account, multi-user shops with roles
- E-mailed invitations and weekly summary
- Rotating refresh tokens in an HttpOnly cookie
- PostgreSQL rate limiter shared by all replicas
- nginx serving the static frontend
- Frontend unit and component tests

Next:

1. Password reset by e-mail.
2. Refund amounts per return from the escrow statement (today returns are counted in
   units, not in money).
3. Validation of the integration against a live Shopee shop (it is built and tested
   against the documented API and a faithful fake, see SECURITY.md).

## Documentation

- [Architecture](docs/architecture.md): components, request flow, data model, Shopee sync and the LLM feature
- [Architecture decision records](docs/adr/README.md): why PostgreSQL is the queue, how the LLM stays grounded, and more
- [SECURITY.md](SECURITY.md): security model and known limitations

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md), the [Code of Conduct](CODE_OF_CONDUCT.md) and the
[CHANGELOG](CHANGELOG.md).

## License

MIT, see [LICENSE](LICENSE).
