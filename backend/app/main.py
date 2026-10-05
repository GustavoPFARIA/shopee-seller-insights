"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import alerts, auth, members, metrics, products, summary, uploads
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    public_docs = settings.app_env != "production"
    app = FastAPI(
        title="Shopee Seller Insights API",
        version="0.2.0",
        docs_url="/api/docs" if public_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if public_docs else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    )
    app.include_router(auth.router)
    app.include_router(uploads.router)
    app.include_router(products.router)
    app.include_router(metrics.router)
    app.include_router(alerts.router)
    app.include_router(summary.router)
    app.include_router(members.router)

    @app.get("/api/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
