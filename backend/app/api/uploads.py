"""Order report upload endpoints."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, DbSession, rate_limit
from app.models import Upload
from app.schemas import UploadOut, UploadResult
from app.services.importer import ImportValidationError, import_orders, parse_file

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

upload_limit = rate_limit(
    "upload",
    lambda: get_settings().upload_rate_limit,
    lambda: get_settings().upload_rate_window_seconds,
)


async def read_limited(file: UploadFile) -> bytes:
    """Read an upload, refusing anything above MAX_UPLOAD_MB without buffering it all."""
    max_mb = get_settings().max_upload_mb
    max_bytes = max_mb * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"File exceeds the {max_mb} MB limit"
        )
    return content


@router.post(
    "",
    response_model=UploadResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(upload_limit)],
    responses={413: {"description": "File too large"}, 422: {"description": "Invalid file"}},
)
async def upload_orders(
    file: UploadFile, user: CurrentUser, db: DbSession
) -> UploadResult | JSONResponse:
    settings = get_settings()
    content = await read_limited(file)
    filename = file.filename or "upload"
    try:
        rows = parse_file(content, filename, settings.max_upload_rows)
    except ImportValidationError as exc:
        # Row errors only carry line number, column and reason: no cell values (may be PII).
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": exc.message, "errors": exc.errors},
        )
    summary = import_orders(
        db,
        seller_id=user.seller_id,
        user_id=user.id,
        filename=filename,
        content=content,
        rows=rows,
    )
    up = summary.upload
    return UploadResult(
        upload_id=up.id,
        row_count=up.row_count,
        orders_created=up.orders_created,
        orders_updated=up.orders_updated,
        orders_unchanged=up.orders_unchanged,
        products_created=summary.products_created,
    )


@router.get("", response_model=list[UploadOut])
def list_uploads(user: CurrentUser, db: DbSession) -> list[Upload]:
    return list(
        db.scalars(
            select(Upload)
            .where(Upload.seller_id == user.seller_id)
            .order_by(Upload.created_at.desc())
            .limit(50)
        )
    )
