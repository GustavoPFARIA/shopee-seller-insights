# Shopee Seller Insights

[![CI](https://github.com/GustavoPFARIA/shopee-seller-insights/actions/workflows/ci.yml/badge.svg)](https://github.com/GustavoPFARIA/shopee-seller-insights/actions/workflows/ci.yml)

Sales analytics for small Shopee sellers. Connect a shop through the **Shopee Open
Platform API** or upload the order report from Seller Centre. The app then shows revenue,
**real margin per product** (after Shopee commission, service and transaction fees,
seller-paid shipping, coupons and product cost), the ABC curve, period comparison and
alerts you can act on.

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
  - A background worker imports orders every 30 minutes. A manual "Sync now" button
    does the same on demand.
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
- **Alerts**: low stock, stalled products (no sales in N days while stock is on hand),
  and margin below a threshold over the last 30 days.
- **Cost and stock spreadsheet**: download the catalogue, fill in cost and stock in
  Excel or Sheets, and upload it back. Empty cells keep the current value.
- **Teams**: a shop can have several users with **owner / manager / viewer** roles,
  invited through one-time links.
- **CSV export** of product metrics, protected against spreadsheet formula injection.
- **Optional AI weekly summary** with Claude (`claude-haiku-4-5`). It is enabled only
  when `ANTHROPIC_API_KEY` is set, and the model gets **aggregates only**.
- **Demo data**:
  - A deterministic seed generates a realistic, fully fake Seller Centre export and
    loads it **through the same importer** that user uploads go through.
  - The fake data has Zipf-like product popularity, weekly seasonality, the real fee
    structure and a realistic mix of order statuses.

## Screenshots

| Dashboard | Products (cost, stock, spreadsheet) |
|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Products](docs/screenshots/products.png) |

| Shopee integration | Team and invitations |
|---|---|
| ![Shopee](docs/screenshots/shopee.png) | ![Team](docs/screenshots/team.png) |

| Upload | Mobile, dark mode |
|---|---|
| ![Upload](docs/screenshots/upload.png) | ![Mobile](docs/screenshots/dashboard-mobile-dark.png) |

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
frontend/
  src/              React app (api.ts typed client, pages/, components/)
  nginx.conf        static serving, /api proxy, security headers
db/init/            creates the least-privilege application role
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
   - The callback checks a single-use `state` (CSRF), exchanges the code for tokens and
     stores the tokens **encrypted**.
   - A Shopee shop can be linked to only one account.
3. **Order sync.**
   - Orders are listed by `update_time` in 15-day windows, which is the API limit. The
     first sync backfills 90 days.
   - Details come from `get_order_detail` in batches of 50.
   - Exact fees come from `get_escrow_detail`: commission, service + transaction fee,
     seller voucher, and shipping net of the Shopee rebate and the buyer-paid part.
   - Orders still in progress are re-checked until the escrow statement is final.
   - **The recipient address is never requested.** The buyer username is pseudonymized,
     as with uploads.
4. **Stock sync.** `get_item_list`, then `get_item_base_info`, then `get_model_list`.
   Stock is matched to the catalogue by SKU.
5. **Scheduling.**
   - The `worker` container syncs every `SHOPEE_SYNC_INTERVAL_MINUTES` minutes. A
     PostgreSQL advisory lock prevents two syncs of the same shop at once, for example
     the worker and "Sync now".
   - Expired access tokens are refreshed automatically. When the 30-day refresh token
     expires, the run is recorded as `reauthorization_required`.
   - Every run is listed on the Shopee tab.

Without these credentials the integration stays off, the worker sits idle and manual
uploads keep working.

## Security & data protection

| Concern | Decision |
|---|---|
| Authentication | **Argon2id** password hashes. Short-lived JWT access token (15 min), kept **in memory only** in the browser. Login does a dummy hash for unknown e-mails, so timing does not reveal which e-mails exist. |
| Sessions | The refresh token is opaque, sits in an **HttpOnly, SameSite=Strict** cookie scoped to `/api/auth`, and is stored only as SHA-256. It **rotates on every use**. Reusing a token that was already rotated revokes the whole session family, since that signals theft. `/refresh` and `/logout` also require an `X-Requested-With` header against CSRF. Production refuses `COOKIE_SECURE=false`. |
| Authorization | Each request checks the user's role, read from the database (owner, manager, viewer), so a role change or removal applies at once. Removing a member also revokes their sessions. A shop can never lose its last owner. |
| Tenant isolation | Every query filters by the authenticated user's `seller_id`, plus a second filter on joined tables. A foreign id answers 404, like a missing one. Covered by tests on every endpoint. |
| Secrets | Read only from the environment via `pydantic-settings`. `.env` is git-ignored. The API **refuses to start** with the compose `dev-only` defaults when `APP_ENV=production`. gitleaks scans the full git history in CI. |
| Third-party tokens | Shopee access and refresh tokens are encrypted with Fernet (`TOKEN_ENCRYPTION_KEY`). HTTP client loggers are capped at WARNING, because Shopee puts `access_token` in the query string. A regression test checks that no token reaches the logs. |
| OAuth | The `state` is single-use, expires in 10 minutes and is stored as SHA-256. The callback redirects only with fixed error codes. |
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

## How to run

Requirements: Docker with Compose v2.

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

On first start the API applies the migrations and loads ~120 days of fake orders.
Swagger UI is at http://localhost:8080/api/docs. To try an upload, use
[`docs/sample-orders.csv`](docs/sample-orders.csv), which is fake data in the Seller
Centre format.

For anything beyond a local demo:

```bash
cp .env.example .env   # then fill in real secrets (and Shopee credentials, if any)
```

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

## Tests and quality

```bash
cd backend
ruff check . && ruff format --check . && mypy app tests   # mypy in strict mode
pytest                                                     # fails below 85% coverage
cd ../frontend && npm run lint && npm run build
```

The backend suite has 155 tests at ~99% coverage. It runs against a **real
PostgreSQL 16**, using the real Alembic migrations. It covers:

- idempotent re-upload and sync
- fee allocation and margin math, checked by hand
- ABC thresholds and local-day boundaries
- cross-seller isolation on every endpoint
- auth failures and refresh-token reuse detection
- role permissions
- rate limits
- oversized, disguised and formula-injection uploads
- PII never stored
- the AI payload carrying aggregates only

The Shopee tests run against a **fake Shopee Open Platform**
([`tests/fake_shopee.py`](backend/tests/fake_shopee.py)). Like the real API, it checks
every HMAC signature and timestamp, enforces the 15-day window, paginates and can inject
failures such as 429s and expired tokens.

CI (GitHub Actions) runs these jobs:

- backend lint and strict typecheck
- tests with a PostgreSQL service, plus `alembic check` (migrations match the models)
- frontend lint and build
- Docker image build
- `pip-audit` and `npm audit`
- a gitleaks scan of the full git history

## Roadmap

Done:

- Shopee Open Platform integration: OAuth, order, escrow and stock sync, scheduled worker
- Spreadsheet import of product cost and stock
- Rotating refresh tokens in an HttpOnly cookie
- PostgreSQL rate limiter shared by all replicas
- nginx serving the static frontend
- Multi-user shops with roles

Next:

1. Shopee push notifications (webhooks) for near real-time order updates, in addition to polling.
2. Returns and refunds detail from the escrow statement, to compute net margin per return.
3. E-mail delivery of invitations and of the weekly summary (needs an SMTP provider).
4. Frontend component tests (Vitest + Testing Library).
5. Several shops per account (for example, one per country).

## License

MIT, see [LICENSE](LICENSE).
