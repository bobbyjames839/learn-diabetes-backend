from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import Profile, utcnow
from app.schemas import OnboardingIn, ProfileOut, ProfileUpdate

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


@router.post("/me/onboarding", response_model=ProfileOut)
def submit_onboarding(
    payload: OnboardingIn,
    profile: Profile = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Profile:
    """Records the one-time onboarding quiz.

    All five answers land together — `onboarding_completed_at` is what the
    frontend checks to decide whether to show the quiz, so it's only ever set
    once every field has a value.
    """
    profile.onboarding_goal = payload.goal
    profile.onboarding_experience = payload.experience
    profile.onboarding_learning_style = payload.learning_style
    profile.onboarding_content_preference = payload.content_preference
    profile.onboarding_focus = payload.focus
    profile.onboarding_completed_at = utcnow()
    db.commit()
    return profile
