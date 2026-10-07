@echo off
rem One-click start for Windows: checks Docker, starts the stack and opens the app.
setlocal
cd /d "%~dp0"
if "%WEB_PORT%"=="" set WEB_PORT=8080

where docker >nul 2>nul || goto :nodocker
docker info >nul 2>nul || goto :notrunning

echo.
echo [Seller Insights] Starting on http://localhost:%WEB_PORT%
echo [Seller Insights] The first start builds the images and takes 5-10 minutes.
echo [Seller Insights] Sign in with demo@shopee-insights.dev / DemoPassword123!
echo [Seller Insights] Close this window or press Ctrl+C to stop.
echo.
start "" /b powershell -NoProfile -Command "for ($i=0; $i -lt 600; $i++) { try { Invoke-WebRequest -UseBasicParsing http://localhost:%WEB_PORT%/api/health | Out-Null; Start-Process http://localhost:%WEB_PORT%; break } catch { Start-Sleep 2 } }"
docker compose up --build
goto :eof

:nodocker
echo [Seller Insights] Docker was not found. Install Docker Desktop from
echo https://www.docker.com/products/docker-desktop/ , restart the computer and run this again.
pause
exit /b 1

:notrunning
echo [Seller Insights] Docker is installed but not running. Open Docker Desktop,
echo wait for "Engine running" and run this again.
pause
exit /b 1
