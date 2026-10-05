#!/usr/bin/env bash
# One command to verify the whole project: quality, tests, security, end-to-end
# behaviour and performance. CI runs the same stages.
#
#   scripts/verify.sh            # all stages
#   scripts/verify.sh static     # lint, format, strict typing, frontend lint + build
#   scripts/verify.sh test       # backend tests with coverage (real PostgreSQL)
#   scripts/verify.sh security   # dependency audits + secret scan
#   scripts/verify.sh e2e        # fresh docker compose stack + smoke test
#   scripts/verify.sh perf       # benchmark on a large synthetic dataset
#
# Requirements: Docker, Python 3.12 with backend/requirements-dev.txt installed
# (or a backend/.venv), Node 22 with frontend dependencies installed.
#
# Environment:
#   TEST_DATABASE_URL  disposable database for test/perf (default: a temporary
#                      PostgreSQL container started and removed by this script)
#   E2E_SKIP_BUILD=1   reuse already built images in the e2e stage
#   PERF_ORDERS        orders in the benchmark (default 50000; 200000 for a full run)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
PY="$BACKEND/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
BIN="$(dirname "$PY")"

E2E_PROJECT="ssi-verify"
E2E_PORT="${E2E_PORT:-18080}"
TEMP_DB=""
FAILED=()

bold() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
run() {
  local name="$1"; shift
  printf -- '-> %s\n' "$name"
  if "$@"; then printf '   ok\n'; else printf '   FAILED: %s\n' "$name"; FAILED+=("$name"); fi
}

cleanup() {
  if [[ -n "$TEMP_DB" ]]; then docker rm -f "$TEMP_DB" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

ensure_test_db() {
  if [[ -n "${TEST_DATABASE_URL:-}" ]]; then return; fi
  TEMP_DB="ssi-verify-db-$$"
  docker run -d --rm --name "$TEMP_DB" -e POSTGRES_PASSWORD=postgres \
    -e POSTGRES_DB=shopee_insights_test -p 127.0.0.1::5432 postgres:16-alpine >/dev/null
  local port
  port="$(docker port "$TEMP_DB" 5432/tcp | head -1 | awk -F: '{print $NF}')"
  for _ in $(seq 1 30); do
    docker exec "$TEMP_DB" pg_isready -U postgres >/dev/null 2>&1 && break
    sleep 1
  done
  sleep 1
  export TEST_DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:$port/shopee_insights_test"
}

stage_static() {
  bold "Static checks"
  cd "$BACKEND"
  run "ruff lint" "$BIN/ruff" check .
  run "ruff format" "$BIN/ruff" format --check .
  run "mypy --strict" "$BIN/mypy" app tests benchmarks devtools
  run "smoke test lint" "$BIN/ruff" check --config "$BACKEND/pyproject.toml" "$ROOT/scripts"
  cd "$FRONTEND"
  run "frontend lint" npm run --silent lint
  run "frontend unit tests (Vitest)" npm run --silent test
  run "frontend type check + build" npm run --silent build
}

stage_test() {
  bold "Backend tests (real PostgreSQL)"
  ensure_test_db
  cd "$BACKEND"
  run "pytest with >=85% coverage" "$BIN/pytest" -q
  run "migrations match models" env MIGRATION_DATABASE_URL="$TEST_DATABASE_URL" \
    "$BIN/alembic" check
  run "migrations downgrade to base" env MIGRATION_DATABASE_URL="$TEST_DATABASE_URL" \
    "$BIN/alembic" downgrade base
  run "migrations upgrade to head" env MIGRATION_DATABASE_URL="$TEST_DATABASE_URL" \
    "$BIN/alembic" upgrade head
}

stage_security() {
  bold "Security"
  cd "$ROOT"
  run "pip-audit (backend dependencies)" "$BIN/pip-audit" -r backend/requirements.txt
  run "npm audit (frontend dependencies)" bash -c "cd '$FRONTEND' && npm audit --audit-level=high"
  run "gitleaks (full git history)" docker run --rm -v "$ROOT:/repo" \
    zricethezav/gitleaks:v8.28.0 git /repo --redact --no-banner --exit-code 1
  run "no secrets or .env committed" bash -c \
    "! git -C '$ROOT' ls-files | grep -E '(^|/)\.env$|\.pem$|id_rsa'"
}

stage_e2e() {
  bold "End-to-end (fresh docker compose stack on port $E2E_PORT)"
  cd "$ROOT"
  export WEB_PORT="$E2E_PORT" FRONTEND_SUBNET="172.30.0.0/24" WEB_IP="172.30.0.10"
  local compose=(docker compose -p "$E2E_PROJECT")
  "${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  local build_flag="--build"
  [[ "${E2E_SKIP_BUILD:-}" == "1" ]] && build_flag="--no-build"
  if ! "${compose[@]}" up -d "$build_flag" --wait --wait-timeout 300; then
    "${compose[@]}" logs --tail 80 || true
    FAILED+=("e2e stack start")
  else
    run "smoke test (52 checks through nginx)" "$PY" scripts/smoke_test.py \
      --base-url "http://localhost:$E2E_PORT"
    run "worker container is running" bash -c \
      "[[ \$(${compose[*]} ps -q --status running worker | wc -l) -eq 1 ]]"
    run "API logs contain no tokens or passwords" bash -c \
      "! ${compose[*]} logs api worker 2>&1 | grep -Eiq 'access_token=|DemoPassword|refresh_token='"
  fi
  "${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}

stage_perf() {
  bold "Performance (synthetic dataset)"
  ensure_test_db
  cd "$BACKEND"
  run "benchmark within budget" "$PY" -m benchmarks.benchmark \
    --orders "${PERF_ORDERS:-50000}" --budget-ms "${PERF_BUDGET_MS:-500}"
}

stages=("$@")
[[ ${#stages[@]} -eq 0 ]] && stages=(static test security e2e perf)
for stage in "${stages[@]}"; do
  case "$stage" in
    static|test|security|e2e|perf) "stage_$stage" ;;
    *) echo "Unknown stage: $stage (use static, test, security, e2e, perf)"; exit 2 ;;
  esac
done

bold "Summary"
if [[ ${#FAILED[@]} -gt 0 ]]; then
  printf 'FAILED (%d):\n' "${#FAILED[@]}"
  printf '  - %s\n' "${FAILED[@]}"
  exit 1
fi
echo "All checks passed: ${stages[*]}"
