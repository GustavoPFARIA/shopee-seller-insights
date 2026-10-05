"""Shop settings: name, alert thresholds and e-mail preferences."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import get_settings
from app.deps import CurrentUser, DbSession, EditorUser, rate_limit
from app.mailer import MailError, send_email
from app.models import Seller
from app.schemas import ShopSettings, ShopSettingsUpdate
from app.services.digest import build_digest
from app.timeutil import today_local

router = APIRouter(prefix="/api/settings", tags=["settings"])

preview_limit = rate_limit(
    "digest-preview",
    lambda: get_settings().upload_rate_limit,
    lambda: get_settings().upload_rate_window_seconds,
)


def _seller(db: DbSession, seller_id: int) -> Seller:
    seller = db.get(Seller, seller_id)
    if seller is None:  # pragma: no cover (a user always belongs to a shop)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shop not found")
    return seller


def _out(seller: Seller) -> ShopSettings:
    out = ShopSettings.model_validate(seller)
    out.email_available = get_settings().email_enabled
    return out


@router.get("", response_model=ShopSettings)
def get_shop_settings(user: CurrentUser, db: DbSession) -> ShopSettings:
    return _out(_seller(db, user.seller_id))


@router.patch("", response_model=ShopSettings)
def update_settings(body: ShopSettingsUpdate, user: EditorUser, db: DbSession) -> ShopSettings:
    seller = _seller(db, user.seller_id)
    for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(seller, field, value)
    db.commit()
    db.refresh(seller)  # return what PostgreSQL stored (e.g. NUMERIC rounding)
    return _out(seller)


@router.post(
    "/digest/preview",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(preview_limit)],
)
def send_digest_preview(user: EditorUser, db: DbSession) -> None:
    """E-mail this week's digest to the requesting user only (to check the format)."""
    if not get_settings().email_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "E-mail is not configured")
    subject, body = build_digest(db, _seller(db, user.seller_id), today_local())
    try:
        send_email([user.email], subject, body)
    except MailError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "E-mail delivery failed") from exc
