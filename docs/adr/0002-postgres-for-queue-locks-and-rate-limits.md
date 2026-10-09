# 0002. PostgreSQL is the queue, the lock service and the rate limiter

**Status:** Accepted

## Context

The app needs a background job queue (Shopee syncs), a guarantee that one shop never syncs twice at once, and rate limits shared by every API replica. Redis plus a task queue is the common answer, but each new service is more to run, secure and back up.

## Decision

Use PostgreSQL for all three:

- **Queue:** `sync_runs` rows with status `queued`, claimed with `SELECT … FOR UPDATE SKIP LOCKED`.
- **Locks:** `pg_advisory_lock` per shop around each sync.
- **Rate limits:** a `rate_limit_hits` table keyed by an HMAC of the client IP and route.

## Consequences

- One stateful service. Backups and access control cover everything.
- Correct under several API replicas and workers.
- Fine at this scale (hundreds of shops). At much higher volume a dedicated queue and an in-memory limiter would be cheaper per request.
