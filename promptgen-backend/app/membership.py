from fastapi import Depends, Header, HTTPException, Request, status

from app.config import settings
from app.auth import get_current_user
from app.db import supabase as _supabase
from app.gym_scope import GymLookupError, resolve_gym_id


def _extract_gym_slug(request: Request, x_gym_slug: str | None) -> str | None:
    """
    Slug can arrive two ways, checked in this order:
    1. `X-Gym-Slug` header - what the frontend should send on every
       authenticated call once a member has logged in via a gym-specific
       link (see gym-scope.js / localStorage['gymSlug']).
    2. `?gym=` query param - convenience for routes hit directly (e.g. a
       bookmarked link, or a manual test), same as the `/member/login?gym=`
       pattern the dev-console's link generator already produces.
    Header wins if both are present. Neither present -> None, and the
    caller falls back to the legacy single-tenant demo gym.
    """
    if x_gym_slug:
        return x_gym_slug
    return request.query_params.get("gym")


def get_or_join_member(
    request: Request,
    user: dict = Depends(get_current_user),
    x_gym_slug: str | None = Header(default=None, alias="X-Gym-Slug"),
) -> dict:
    """
    Ensures the logged-in Supabase auth user has a `members` row for their
    gym, creating one (if there's room) on first login. Raises 403 if the
    gym is at capacity.

    Every route that should be gym-scoped depends on this instead of just
    get_current_user.

    Gym scoping: resolves a slug (see _extract_gym_slug) to a gym_id via
    gym_scope.resolve_gym_id(), falling back to settings.demo_gym_id when no
    slug is supplied - this keeps every existing single-tenant caller
    working unchanged. A slug that doesn't resolve to a real gym is a 404,
    not a silent fallback (see gym_scope.GymLookupError docstring for why).
    """
    auth_user_id = user.get("sub")
    slug = _extract_gym_slug(request, x_gym_slug)

    try:
        gym_id = resolve_gym_id(slug)
    except GymLookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    try:
        result = _supabase.rpc(
            "join_gym_if_capacity",
            {"p_gym_id": gym_id, "p_auth_user_id": auth_user_id},
        ).execute()
    except Exception as e:
        if "gym_full" in str(e):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This gym has reached its member limit. Contact your gym.",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Membership check failed: {e}",
        )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not join gym.",
        )

    member = result.data
    if member.get("status") != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been deactivated. Contact your gym.",
        )

    return member
