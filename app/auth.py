"""Supabase JWT verification.

The frontend holds the session and sends `Authorization: Bearer <access_token>`.
The backend never sees a password — it only verifies the token's signature.

Supabase signs either with a project JWT secret (legacy, HS256) or with a rotating
key published at the JWKS endpoint (ES256/RS256). Both are supported; the JWKS path
is tried first because new projects use it.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWKClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import flashcards
from app.config import get_settings
from app.db import get_db
from app.models import Profile

logger = logging.getLogger(__name__)

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
    headers={"WWW-Authenticate": "Bearer"},
)


@lru_cache
def _jwk_client() -> PyJWKClient | None:
    settings = get_settings()
    if not settings.supabase_url:
        return None
    # PyJWKClient caches keys in-process and refetches when a kid is unknown.
    return PyJWKClient(settings.jwks_url, cache_keys=True)


def _decode(token: str) -> dict:
    settings = get_settings()
    options = {"verify_aud": False}  # Supabase uses aud="authenticated"

    client = _jwk_client()
    if client is not None:
        try:
            key = client.get_signing_key_from_jwt(token).key
            return jwt.decode(
                token,
                key,
                algorithms=["ES256", "RS256", "EdDSA"],
                options=options,
            )
        except jwt.PyJWTError:
            raise
        except Exception as exc:  # JWKS unreachable, or project is HS256-only
            logger.debug("JWKS verification unavailable, trying shared secret: %s", exc)

    if settings.supabase_jwt_secret:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            options=options,
        )

    raise RuntimeError(
        "No way to verify tokens: set SUPABASE_URL (for JWKS) or SUPABASE_JWT_SECRET."
    )


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise CREDENTIALS_ERROR
    return token


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> Profile:
    """Verify the bearer token and return the caller's profile, creating it on first use."""
    token = _bearer_token(request)

    try:
        claims = _decode(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except jwt.PyJWTError as exc:
        logger.info("rejected token: %s", exc)
        raise CREDENTIALS_ERROR from None
    except RuntimeError as exc:
        logger.error("auth misconfigured: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication is not configured on the server.",
        ) from None

    user_id = claims.get("sub")
    if not user_id:
        raise CREDENTIALS_ERROR

    profile = db.get(Profile, user_id)
    if profile is not None:
        return profile

    # First request after signup — mirror the auth user into our own table.
    #
    # This runs concurrently with itself. The home page asks for /api/me and
    # /api/stats in one `Promise.all`, so a brand new reader's *first* action is
    # two simultaneous first-requests, both finding no profile and both trying
    # to create one. Whoever loses that race used to get a duplicate-key 500 on
    # the very first screen of the app.
    db.add(
        Profile(
            id=user_id,
            email=claims.get("email"),
            display_name=(claims.get("user_metadata") or {}).get("display_name"),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # The other request created it a moment ago. That is a success for this
        # one: the profile exists, which is all it wanted. It must not seed the
        # deck as well — the winner is doing that.
        db.rollback()
        profile = db.get(Profile, user_id)
        if profile is None:
            raise
        logger.info("profile for %s was created concurrently", user_id)
        return profile

    profile = db.get(Profile, user_id)
    flashcards.seed_default(db, profile.id)
    return profile
