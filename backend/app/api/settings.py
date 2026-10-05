"""Shop settings: name, alert thresholds and e-mail preferences."""

from fastapi import APIRouter, HTTPException, status

from app.deps import CurrentUser, DbSession, EditorUser
from app.models import Seller
from app.schemas import ShopSettings, ShopSettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _seller(db: DbSession, seller_id: int) -> Seller:
    seller = db.get(Seller, seller_id)
    if seller is None:  # pragma: no cover (a user always belongs to a shop)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shop not found")
    return seller


@router.get("", response_model=ShopSettings)
def get_settings_(user: CurrentUser, db: DbSession) -> Seller:
    return _seller(db, user.seller_id)


@router.patch("", response_model=ShopSettings)
def update_settings(body: ShopSettingsUpdate, user: EditorUser, db: DbSession) -> Seller:
    seller = _seller(db, user.seller_id)
    for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(seller, field, value)
    db.commit()
    db.refresh(seller)  # return what PostgreSQL stored (e.g. NUMERIC rounding)
    return seller
