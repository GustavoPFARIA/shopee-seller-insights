# Architecture decision records

Short records of the main technical decisions: the context, what was decided and what it costs.

| # | Decision |
|---|---|
| [0001](0001-grounded-llm-summary.md) | The LLM explains pre-computed aggregates and never sees raw data |
| [0002](0002-postgres-for-queue-locks-and-rate-limits.md) | PostgreSQL is the queue, the lock service and the rate limiter |
| [0003](0003-idempotent-imports.md) | Imports and syncs are idempotent and set-based |
| [0004](0004-access-token-in-memory-refresh-in-cookie.md) | Access token in memory, rotating refresh token in an HttpOnly cookie |
| [0005](0005-fake-shopee-for-tests-and-demo.md) | A faithful fake Shopee for tests and the demo |
