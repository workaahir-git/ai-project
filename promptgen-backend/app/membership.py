from fastapi import Depends, HTTPException, status
from supabase import create_client, Client

from app.config import settings
from app.auth import get_current_user

# Service-role client: server-side only, bypasses RLS by design.
# NEVER import this key or client into anything that ships to the browser.
_supabase: Client = create_client(
    settings.supabase_url,
    settings.supabase_service_role_key,
)


def get_or_join_member(user: dict = Depends(get_current_user)) -> dict:
    """
    Ensures the logged-in Supabase auth user has a `members` row for the
    demo gym, creating one (if there's room) on first login. Raises 403
    if the gym is at capacity.

    Every route that should be gym-scoped depends on this instead of
    just get_current_user.
    """
    auth_user_id = user.get("sub")

    try:
        result = _supabase.rpc(
            "join_gym_if_capacity",
            {"p_gym_id": settings.demo_gym_id, "p_auth_user_id": auth_user_id},
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
