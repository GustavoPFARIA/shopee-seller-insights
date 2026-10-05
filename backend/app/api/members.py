"""Team management for a shop (owners only, except listing)."""

from typing import NoReturn

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete, select

from app.deps import CurrentUser, DbSession, OwnerUser
from app.models import Invitation, User
from app.schemas import InvitationCreate, InvitationCreated, InvitationOut, MemberOut, MemberUpdate
from app.services import members

router = APIRouter(prefix="/api/members", tags=["members"])


def _raise(exc: members.MembershipError) -> NoReturn:
    raise HTTPException(exc.status_code, exc.message) from exc


@router.get("", response_model=list[MemberOut])
def list_members(user: CurrentUser, db: DbSession) -> list[User]:
    return list(
        db.scalars(select(User).where(User.seller_id == user.seller_id).order_by(User.created_at))
    )


@router.patch("/{user_id}", response_model=MemberOut)
def update_member(user_id: int, body: MemberUpdate, owner: OwnerUser, db: DbSession) -> User:
    try:
        return members.change_role(db, seller_id=owner.seller_id, user_id=user_id, role=body.role)
    except members.MembershipError as exc:
        _raise(exc)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_member(user_id: int, owner: OwnerUser, db: DbSession) -> Response:
    try:
        members.remove_member(db, seller_id=owner.seller_id, user_id=user_id)
    except members.MembershipError as exc:
        _raise(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/invitations", response_model=list[InvitationOut])
def list_invitations(owner: OwnerUser, db: DbSession) -> list[Invitation]:
    return list(
        db.scalars(
            select(Invitation)
            .where(Invitation.seller_id == owner.seller_id, Invitation.accepted_at.is_(None))
            .order_by(Invitation.created_at.desc())
        )
    )


@router.post("/invitations", response_model=InvitationCreated, status_code=status.HTTP_201_CREATED)
def invite(body: InvitationCreate, owner: OwnerUser, db: DbSession) -> InvitationCreated:
    try:
        invitation, token = members.create_invitation(
            db,
            seller_id=owner.seller_id,
            invited_by=owner.id,
            email=body.email,
            role=body.role,
        )
    except members.MembershipError as exc:
        _raise(exc)
    return InvitationCreated(
        id=invitation.id,
        email=invitation.email,
        role=body.role,
        expires_at=invitation.expires_at,
        token=token,
    )


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invitation(invitation_id: int, owner: OwnerUser, db: DbSession) -> Response:
    result = db.execute(
        delete(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.seller_id == owner.seller_id,
            Invitation.accepted_at.is_(None),
        )
    )
    db.commit()
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
