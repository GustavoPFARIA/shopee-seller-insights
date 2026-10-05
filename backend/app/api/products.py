"""Product catalogue endpoints (cost and stock are informed by the seller)."""

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from app.api.uploads import read_limited, upload_limit
from app.config import get_settings
from app.csv_safety import safe_cell
from app.deps import CurrentUser, DbSession
from app.models import Product
from app.schemas import CatalogImportResultOut, ProductOut, ProductUpdate
from app.services.catalog_import import TEMPLATE_HEADER, apply_catalog, parse_catalog
from app.services.importer import ImportValidationError

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("", response_model=list[ProductOut])
def list_products(user: CurrentUser, db: DbSession) -> list[Product]:
    return list(
        db.scalars(select(Product).where(Product.seller_id == user.seller_id).order_by(Product.sku))
    )


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: int, body: ProductUpdate, user: CurrentUser, db: DbSession
) -> Product:
    product = db.scalar(
        select(Product).where(Product.id == product_id, Product.seller_id == user.seller_id)
    )
    if product is None:
        # Same answer for "does not exist" and "belongs to someone else".
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    db.commit()
    return product


@router.get("/template.csv", response_class=Response)
def catalog_template(user: CurrentUser, db: DbSession) -> Response:
    """Current catalogue as a spreadsheet, ready to fill in and re-upload."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(TEMPLATE_HEADER)
    for p in db.scalars(
        select(Product).where(Product.seller_id == user.seller_id).order_by(Product.sku)
    ):
        writer.writerow(
            [
                safe_cell(p.sku),
                safe_cell(p.name),
                "" if p.unit_cost is None else p.unit_cost,
                "" if p.stock_quantity is None else p.stock_quantity,
                p.low_stock_threshold,
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="products.csv"'},
    )


@router.post(
    "/import",
    response_model=CatalogImportResultOut,
    dependencies=[Depends(upload_limit)],
    responses={413: {"description": "File too large"}, 422: {"description": "Invalid file"}},
)
async def import_catalog(
    file: UploadFile, user: CurrentUser, db: DbSession
) -> CatalogImportResultOut | JSONResponse:
    content = await read_limited(file)
    try:
        rows = parse_catalog(content, file.filename or "upload", get_settings().max_upload_rows)
        result = apply_catalog(db, user.seller_id, rows)
    except ImportValidationError as exc:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": exc.message, "errors": exc.errors},
        )
    return CatalogImportResultOut(
        rows=result.rows, updated=result.updated, created=result.created, unchanged=result.unchanged
    )
