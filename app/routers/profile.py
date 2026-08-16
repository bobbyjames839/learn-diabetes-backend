from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import Profile
from app.schemas import ProfileOut, ProfileUpdate

router = APIRouter()


@router.get("/me", response_model=ProfileOut)
def me(profile: Profile = Depends(get_current_user)) -> Profile:
    return profile


@router.patch("/me", response_model=ProfileOut)
def update_me(
    payload: ProfileUpdate,
    profile: Profile = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Profile:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    db.commit()
    return profile
