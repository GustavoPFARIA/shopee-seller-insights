# Contributing

Thanks for your interest in Shopee Seller Insights. This guide explains how to set up
the project, the rules every change must follow, and how to open a pull request.

## Ground rules

- **No new framework or external service** without discussing it in an issue first.
  The stack is FastAPI, PostgreSQL, SQLAlchemy/Alembic, pandas, React/Vite/Recharts,
  nginx and Docker.
- **Security is not optional.** Read [SECURITY.md](SECURITY.md). In short: every query
  is scoped to the active shop, only ORM or bound parameters, no secrets in code, never
  log personal data, and validate every input.
- **Tests come first.** A bug fix starts with a test that fails without the fix. Never
  weaken or skip a test to make CI green.
- Code, comments, commit messages and documentation are written in **English**.

## Development setup

The quickest way is the full stack:

```bash
docker compose up --build        # http://localhost:8080
```

For backend work without Docker, see "Local development without Docker" in the
[README](README.md#local-development-without-docker). For the frontend:

```bash
cd frontend
npm ci
npm run dev                      # http://localhost:5173, proxies /api to :8000
```

## Checks

Run the same checks as CI before pushing:

```bash
scripts/verify.sh static test    # fast: lint, types, unit tests (backend + frontend)
scripts/verify.sh                # everything: also security, e2e and performance
```

| Area | Command | Requirement |
|---|---|---|
| Python lint / format | `ruff check .` / `ruff format --check .` | clean |
| Python types | `mypy app tests benchmarks devtools` | `--strict`, no errors |
| Backend tests | `pytest` | all pass, coverage ≥ 85% |
| Frontend | `npm run lint && npm test && npm run build` | clean |
| Migrations | `alembic check` | models and migrations match |

## Making a change

1. Open or pick an issue, and create a branch from `main`
   (`feat/short-name`, `fix/short-name`, `docs/short-name`).
2. Write the test, then the code. Keep the change focused on one thing.
3. Database changes need an **Alembic migration** with a working `downgrade`.
4. New settings go in `app/config.py`, `.env.example` and the README configuration table.
5. Update the README, SECURITY.md and [CHANGELOG.md](CHANGELOG.md) when behaviour changes.
6. Run `scripts/verify.sh static test`.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(products): bulk cost import from CSV/XLSX
fix(security): do not trust X-Forwarded-For
docs: explain the push notification signature
```

Types: `feat`, `fix`, `perf`, `test`, `docs`, `ci`, `refactor`, `chore`.

## Pull requests

- Fill in the pull request template.
- CI must be green: lint, types, tests, e2e smoke test, performance budget, dependency
  audit and secret scan.
- Add screenshots for visible UI changes.
- One approving review is needed before merging.

## Reporting bugs and security issues

Use the issue templates for bugs and feature requests. **Do not open public issues for
vulnerabilities**: follow [SECURITY.md](SECURITY.md).
