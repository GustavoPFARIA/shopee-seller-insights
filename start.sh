#!/usr/bin/env sh
# One-click start for macOS/Linux: checks Docker, starts the stack and opens the app.
set -e
cd "$(dirname "$0")"
PORT="${WEB_PORT:-8080}"

command -v docker >/dev/null 2>&1 || { echo "Docker was not found. Install Docker Desktop: https://www.docker.com/products/docker-desktop/"; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and try again."; exit 1; }

echo "Starting on http://localhost:$PORT (the first start takes 5-10 minutes)."
echo "Sign in with demo@shopee-insights.dev / DemoPassword123!"
(
  for _ in $(seq 600); do
    if curl -fsS "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
      (command -v open >/dev/null && open "http://localhost:$PORT") || (command -v xdg-open >/dev/null && xdg-open "http://localhost:$PORT") || true
      break
    fi
    sleep 2
  done
) &
exec docker compose up --build
