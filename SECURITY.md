# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report them privately
with GitHub's "Report a vulnerability" button (Security → Advisories) on this
repository. Include steps to reproduce and the impact you observed. You should get an
acknowledgement within 7 days.

## Scope and design

This is a portfolio project, not a hosted service. The main controls are:

- **Authentication**:
  - Argon2id password hashes.
  - JWT access tokens that last 15 minutes and are kept only in browser memory.
  - Refresh tokens in an HttpOnly, SameSite=Strict cookie, rotated on every use, with
    detection of reused tokens.
- **Authorization**:
  - Every query is scoped to the authenticated seller.
  - Owner, manager and viewer roles are checked on every request, using the role
    stored in the database.
  - Cross-tenant access and role permissions are covered by automated tests.
- **Input handling**:
  - Limits on upload type, size, row count and decompressed size.
  - Strict Pydantic validation.
  - Spreadsheet formulas are rejected on import and neutralized on export.
- **Data minimization (LGPD)**:
  - Buyer names, phones and addresses are never stored, and never requested from the
    Shopee API.
  - Buyer usernames are pseudonymized with HMAC-SHA256.
  - The LLM integration receives aggregates only.
- **Third-party credentials**:
  - Shopee tokens are encrypted at rest with Fernet.
  - HTTP client URL logging is disabled, because Shopee sends `access_token` in the
    query string.
  - The OAuth `state` and invitation tokens are single-use and stored hashed.
- **Infrastructure**:
  - The API runs with a least-privilege database role.
  - Neither the database nor the API publishes a port; only nginx is exposed, and it
    sends strict security headers (CSP and others).
  - Containers run as non-root users.
  - Rate limits are stored in PostgreSQL and keyed by HMAC.
- **Supply chain**: `pip-audit`, `npm audit` and gitleaks run in CI on every push.

## Known limitations

- **No TLS in the demo.** The compose stack serves plain HTTP on `localhost:8080`, so
  `COOKIE_SECURE=false` there. A real deployment terminates TLS in front of nginx, and
  the API refuses `COOKIE_SECURE=false` when `APP_ENV=production`.
- **Same-tab JavaScript can still act as the user.** The access token lives in memory,
  so an XSS bug could make API calls while the tab is open, but it cannot steal the
  long-lived refresh token. The CSP (`script-src 'self'`) and React's output escaping
  reduce that risk.
- **30-second refresh grace window.** Refresh-token reuse is tolerated for 30 seconds
  after a rotation, but only while the session is still active. This lets two tabs
  refresh at the same moment without logging the user out. The trade-off: a stolen
  token replayed within those 30 seconds is not detected. Reuse after the window still
  revokes the whole session.
- **Fixed IP for nginx.** `FORWARDED_ALLOW_IPS` trusts nginx at a fixed address on the
  compose network. Other topologies must set it to their own reverse proxy's address.
- **Invitations are not e-mailed.** There is no SMTP; the owner shares the one-time
  link themselves.
- **Not tested against the live Shopee API.** The integration follows the Shopee
  Open Platform v2 documentation and is tested against a fake that checks signatures.
  It has not been run against a live partner account in this repository.
- **Dev-only fallback secrets.** `docker-compose.yml` has them so the demo runs from a
  clean clone. The API refuses them when `APP_ENV=production`.

## Supported versions

Only the latest commit on the default branch receives fixes.
