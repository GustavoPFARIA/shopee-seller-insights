# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report them privately
through GitHub's "Report a vulnerability" button (Security → Advisories) on this
repository. Include steps to reproduce and the impact you observed. You can expect
an acknowledgement within 7 days.

## Scope and design

This is a portfolio project, not a hosted service. The main controls are:

- **Authentication**: Argon2id password hashing and short-lived HS256 JWT access tokens.
- **Authorization**: every query is scoped to the authenticated seller. Cross-tenant
  access is covered by automated tests.
- **Input handling**: upload type, size, row and decompressed-size limits; strict
  Pydantic validation; rejection of spreadsheet formulas on import and neutralization
  on export.
- **Data minimization (LGPD)**: buyer names, phones and addresses are never stored;
  buyer usernames are pseudonymized with HMAC-SHA256. Personal data is never logged
  and never sent to the optional LLM integration, which receives aggregates only.
- **Infrastructure**: least-privilege database role for the API, database not exposed
  outside the Docker network, non-root containers, secrets only from the environment.
- **Supply chain**: `pip-audit`, `npm audit` and gitleaks run in CI on every push.

## Known limitations

- The rate limiter is in-memory, per process. Use a shared store before running
  several API replicas.
- There are no refresh tokens. The access token is kept in `sessionStorage`, so an
  XSS bug could steal it. React escapes output by default and no HTML is rendered from
  user data, but an HttpOnly cookie session would be stronger.
- The `web` container uses `vite preview`, which is not a hardened production server.
- `docker-compose.yml` has dev-only fallback secrets so the demo runs from a clean
  clone. The API refuses to start with them when `APP_ENV=production`.

## Supported versions

Only the latest commit on the default branch receives fixes.
