"""Product catalogue endpoints (cost and stock are informed by the seller)."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.deps import CurrentUser, DbSession
from app.models import Product
from app.schemas import ProductOut, ProductUpdate

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
