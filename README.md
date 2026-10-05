# Shopee Seller Insights

[![CI](https://github.com/GustavoPFARIA/shopee-seller-insights/actions/workflows/ci.yml/badge.svg)](https://github.com/GustavoPFARIA/shopee-seller-insights/actions/workflows/ci.yml)

Sales analytics for small Shopee sellers: upload the order report exported from
Shopee Seller Centre and see revenue, **real margin per product** (after Shopee
commission, service fee, seller-paid shipping, coupons and product cost), ABC curve,
period comparison and actionable alerts.

## The problem

Shopee Seller Centre shows gross sales, but a seller who wants to know *which
products actually make money* has to export spreadsheets and rebuild the math by
hand: fees are order-level, product cost is not in the export, and cancelled or
unpaid orders are mixed with real sales. This app does that math consistently,
keeps history across uploads without duplicating orders, and flags what needs
attention.

## Features

- **Upload** CSV or XLSX order exports (Brazilian Seller Centre headers in Portuguese,
  or the English export). Validated row by row with Pydantic; errors point to line and
  column. **Idempotent**: re-uploading the same or an overlapping report never
  duplicates orders. It only updates status, for example when an order becomes "Cancelado".
- **Metrics**: revenue, orders, units, average ticket, net margin, daily revenue,
  comparison with the previous period of the same length.
- **Real margin per product**: order-level fees are allocated to items in proportion
  to their value (cent-exact), then `margin = revenue − commission − service fee −
  seller shipping − coupons − unit cost × units`. If a product has no cost informed,
  its margin is shown as "not set" instead of a misleading number.
- **ABC curve** (Pareto): A up to 80% of cumulative revenue, B up to 95%, C the rest.
- **Alerts**: low stock, stalled products (no sales in N days with stock on hand),
  margin below a threshold in the last 30 days.
- **CSV export** of product metrics, protected against spreadsheet formula injection.
- **Optional AI weekly summary** with Claude (`claude-haiku-4-5`). It is enabled only
  when `ANTHROPIC_API_KEY` is set and gets **aggregates only** (see
  [Security](#security--data-protection)). Everything else works without it.
- **Demo data**: a deterministic seed that generates a realistic, completely fake
  Seller Centre export (Zipf-like product popularity, weekly seasonality, real fee
  structure, status mix) and loads it **through the same importer** as user uploads.

## Screenshots

| Dashboard | Products (cost & stock) |
|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Products](docs/screenshots/products.png) |

| Upload | Mobile, dark mode |
|---|---|
| ![Upload](docs/screenshots/upload.png) | ![Mobile](docs/screenshots/dashboard-mobile-dark.png) |

## Architecture

```mermaid
flowchart LR
    B[Browser] -->|HTTP :5173| W["web<br/>React + Vite build<br/>(vite preview, proxies /api)"]
    W -->|/api| A["api<br/>FastAPI + Pydantic v2<br/>SQLAlchemy 2.0"]
    A -->|DML-only role ssi_app| D[("db<br/>PostgreSQL 16<br/>no published port")]
    M["alembic upgrade head<br/>(owner role ssi_owner)"] --> D
    A -.->|aggregates only, optional| C["Claude API<br/>claude-haiku-4-5"]
    subgraph docker compose
      W
      A
      D
      M
    end
```

```
backend/
  app/
    api/          FastAPI routers (auth, uploads, products, metrics, alerts, summary)
    services/     importer, metrics, alerts, ai_summary (pure business logic)
    models.py     SQLAlchemy models        schemas.py  Pydantic API schemas
    config.py     pydantic-settings        security.py Argon2, JWT, HMAC pseudonymization
    seed.py       fake demo data generator
  alembic/        migrations (the only way the schema changes)
  tests/          pytest suite, runs against real PostgreSQL
frontend/src/     React app (api.ts typed client, pages/)
db/init/          creates the least-privilege application role
```

## Database schema

```mermaid
erDiagram
    sellers ||--o{ users : has
    sellers ||--o{ products : sells
    sellers ||--o{ uploads : receives
    sellers ||--o{ orders : owns
    uploads ||--o{ orders : "first imported by"
    orders ||--|{ order_items : contains
    products ||--o{ order_items : "sold as"

    sellers {
        int id PK
        string name
        string shop_code UK
        timestamptz created_at
    }
    users {
        int id PK
        int seller_id FK
        string email UK
        string password_hash
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
        string filename
        string file_sha256
        int row_count
        int orders_created
        int orders_updated
        int orders_unchanged
    }
    orders {
        int id PK
        int seller_id FK
        int upload_id FK
        string order_sn
        string status
        timestamptz ordered_at
        string buyer_hash "HMAC, never raw"
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
```

- Unique keys: `products(seller_id, sku)`, `orders(seller_id, order_sn)` (the
  idempotency key), `order_items(order_id, product_id)`, `users(email)`.
- CHECK constraints: no negative prices, costs, fees or stock; `quantity > 0`.
- Money is `NUMERIC(12,2)` / `Decimal` end to end, never float. Timestamps are
  `timestamptz` in UTC. Days are grouped in the seller's time zone
  (`REPORT_TIMEZONE`, default `America/Sao_Paulo`) inside PostgreSQL.
- Index `orders(seller_id, ordered_at)` backs every period query.

## Security & data protection

| Concern | Decision |
|---|---|
| Authentication | JWT access tokens (HS256, 30 min, `exp`/`iat`/`sub` required, `alg=none` rejected). Passwords hashed with **Argon2id**. Login does a dummy hash for unknown e-mails to keep timing similar. |
| Tenant isolation | Every query filters by the authenticated user's `seller_id` (plus a second filter on the joined table as defense in depth). A foreign product id answers 404, the same as a missing one. Covered by tests for every endpoint. |
| Secrets | Only from environment through `pydantic-settings`; `.env` is git-ignored and `.env.example` documents the variables. Compose has clearly marked `dev-only` fallbacks so a clean clone runs, and the API **refuses to start** with them when `APP_ENV=production`. gitleaks scans the full history in CI. |
| Uploads | Extension allow-list (`.csv`, `.xlsx`), max size (`MAX_UPLOAD_MB`, 5 MB), max rows (`MAX_UPLOAD_ROWS`), UTF-8 check, XLSX magic-bytes check and a zip-bomb guard on the uncompressed size. All-or-nothing: an invalid file imports nothing. |
| CSV/formula injection | Text cells starting with `= + - @ \t \r` are rejected on import; the CSV export prefixes such cells with `'`. Identifiers (order id, SKU) must match a strict pattern. |
| SQL injection | SQLAlchemy ORM/Core with bound parameters only. No string-built SQL. |
| Rate limiting | Login/register: 5 per minute per IP. Uploads and AI summary: 10 per hour per IP. HTTP 429 when exceeded. |
| CORS | Restricted to the configured origin. In compose the browser only talks to the `web` origin, which proxies `/api`. |
| Personal data (LGPD) | Recipient name, phone and address columns are dropped before validation and never stored. The buyer username is replaced by an HMAC-SHA256 with a secret key. Validation errors report line, column and reason, never cell values. Nothing logs request bodies. |
| AI | Only an aggregate JSON goes to the model: weekly totals, top-5 product names with revenue/margin, ABC counts and alerted product names. No orders, ids, hashes or buyer data. The exact payload is returned to the user as `facts` for transparency. |
| Database | Migrations run as the schema owner. The API connects as `ssi_app`, which has only `SELECT/INSERT/UPDATE/DELETE` and cannot create, alter or drop. The DB has no published port. Containers run as non-root users. |
| Supply chain | `pip-audit` and `npm audit` in CI; pinned Python dependencies and an npm lockfile. |

See [SECURITY.md](SECURITY.md) for how to report a vulnerability.

## How to run

Requirements: Docker with Compose v2.

```bash
git clone https://github.com/GustavoPFARIA/shopee-seller-insights.git
cd shopee-seller-insights
docker compose up --build
```

Open **http://localhost:5173** and sign in with the demo account:

| E-mail | Password |
|---|---|
| `demo@shopee-insights.dev` | `DemoPassword123!` |

On first start the API applies the migrations and loads ~120 days of fake orders.
A second account (`other@shopee-insights.dev`, same password) holds a different
shop, so you can check that data is isolated. Swagger UI is at
http://127.0.0.1:8000/docs. To try the upload, use
[`docs/sample-orders.csv`](docs/sample-orders.csv). It is fake data in the Seller Centre format.

For anything beyond a local demo:

```bash
cp .env.example .env   # then fill in real secrets
```

To enable the AI summary, set `ANTHROPIC_API_KEY` in `.env`.

### Local development without Docker

```bash
# PostgreSQL for development/tests
docker run -d --name ssi-db -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=shopee_insights_test -p 5433:5432 postgres:16-alpine

cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5433/shopee_insights_test
export MIGRATION_DATABASE_URL=$DATABASE_URL
export JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
export PII_HASH_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
alembic upgrade head && python -m app.seed
uvicorn app.main:app --reload

cd ../frontend && npm ci && npm run dev   # http://localhost:5173 (proxies /api to :8000)
```

## Tests and quality

```bash
cd backend
ruff check . && ruff format --check . && mypy app tests   # mypy runs in strict mode
pytest                                                     # fails below 85% coverage
cd ../frontend && npm run lint && npm run build
```

The backend suite (76 tests, ~98% coverage) runs against a **real PostgreSQL 16**
with the real Alembic migrations. It covers idempotent re-upload, status updates,
fee allocation and margin math checked by hand, ABC thresholds, local-day boundaries,
cross-seller isolation on every endpoint, auth failures (expired, forged, `alg=none`,
deleted user), rate limits, oversized/disguised/formula-injection uploads, PII never
stored, and the AI payload never containing order or buyer data.

CI (GitHub Actions) runs these jobs: backend lint + strict typecheck, tests with a
PostgreSQL service and `alembic check` (migrations match the models), frontend lint +
build, Docker image build, `pip-audit` + `npm audit`, and a gitleaks scan of the full
history.

## Roadmap

1. **Shopee Open Platform API integration**: OAuth shop authorization and scheduled
   order sync (`v2.order.get_order_list` / `get_order_detail`, escrow details for exact
   fees), replacing manual uploads.
2. Product cost/stock import from a spreadsheet, and stock sync from the Open Platform.
3. Refresh tokens with rotation and an HttpOnly cookie session.
4. A shared rate-limit store, so the API can run as several replicas.
5. Static frontend served by a production web server or CDN instead of `vite preview`.
6. Multi-user shops with roles.

## License

MIT, see [LICENSE](LICENSE).
