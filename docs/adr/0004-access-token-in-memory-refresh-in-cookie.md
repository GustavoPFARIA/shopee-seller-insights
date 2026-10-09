# 0004. Access token in memory, rotating refresh token in an HttpOnly cookie

**Status:** Accepted

## Context

Storing a long-lived JWT in `localStorage` lets any XSS steal it. Server-side sessions on every request are simple but add a database read to each call.

## Decision

- A 15-minute access token, kept only in JavaScript memory.
- A refresh token in an HttpOnly, SameSite=Strict cookie scoped to `/api/auth`, rotated on every use, stored hashed. Reusing a rotated token revokes the whole family (with a 30-second grace window for concurrent tabs).
- Refresh requires an `X-Requested-With` header as a CSRF check.

## Consequences

- Script on the page cannot read the refresh token, and a stolen access token expires quickly.
- A page reload costs one refresh call.
- Script running in the page can still act as the user while it runs; that limit is documented in SECURITY.md.
