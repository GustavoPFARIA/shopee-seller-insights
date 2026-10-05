"""Shops the signed-in user belongs to; creating a new shop."""

from fastapi import APIRouter, Depends, status

from app.api.auth import login_limit
from app.deps import CurrentUser, DbSession
from app.schemas import ShopCreate, ShopRef
from app.services import members

router = APIRouter(prefix="/api/shops", tags=["shops"])


@router.post(
    "",
    response_model=ShopRef,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(login_limit)],  # same budget as sign-ups
)
def create_shop(body: ShopCreate, user: CurrentUser, db: DbSession) -> ShopRef:
    """Create another shop; the creator becomes its owner. Switch with X-Shop-Id."""
    seller = members.create_shop(db, user_id=user.id, name=body.name)
    return ShopRef(id=seller.id, name=seller.name, role="owner")
