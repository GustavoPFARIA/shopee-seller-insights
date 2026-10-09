# 0005. A faithful fake Shopee for tests and the demo

**Status:** Accepted

## Context

The Shopee Open Platform needs partner approval, and its sandbox is not something CI or a reviewer can use. Mocking HTTP calls one by one would not test signing, pagination or token refresh.

## Decision

`devtools/fake_shopee.py` implements the v2 endpoints the app uses. Like the real API it checks every HMAC signature and timestamp, enforces the 15-day window, paginates, rotates tokens and can inject failures (429, expired or revoked tokens). The tests use it in-process; `docker-compose.shopee-demo.yml` runs it as a server with a consent page and a button that sends a signed push.

## Consequences

- The whole integration, OAuth to webhook, is tested and can be demoed without credentials.
- The fake encodes our reading of the documentation. It has not been checked against a live shop; SECURITY.md lists this as a known limitation.
