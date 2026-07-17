from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth import verify_member_token
from app.db import supabase as _supabase

bearer_scheme = HTTPBearer()


def _extract_gym_slug(request: Request, x_gym_slug: str | None) -> str | None:
    """
    Slug can arrive two ways, checked in this order:
    1. `X-Gym-Slug` header - what the frontend sends on every call once a
       member has logged in via a gym-specific link (see login.html /
       localStorage['gymSlug']).
    2. `?gym=` query param - convenience for routes hit directly (e.g. a
       bookmarked link, or a manual test), same as the `/member/login?gym=`
       pattern the dev-console's link generator already produces.
    Header wins if both are present. Neither present -> None, and the
    caller falls back to the legacy single-tenant demo gym.
    """
    if x_gym_slug:
        return x_gym_slug
    if request is not None:
        return request.query_params.get("gym")
    return None


def find_member_by_login_code(gym_id: str, code: str) -> dict | None:
    """
    Looks up an existing `members` row by (gym_id, login_code). This is the
    ONLY place a member gets identified for login — there is no signup here.
    The row itself is created exclusively by gym-dashboard's "Add Member"
    flow (POST /admin/members), which is what generates `login_code` in the
    first place. If the code doesn't match a row in this gym, that's not
    "create an account", it's "wrong code" (see main.py's 401 handling).

    The 8-digit code is the only credential — there is no member password.
    """
    res = (
        _supabase.table("members")
        .select("*")
        .eq("gym_id", gym_id)
        .eq("login_code", code)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def get_current_member(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    Replaces the old get_current_user + get_or_join_member pair. There is no
    "user" separate from "member" anymore, and no join-on-first-login step —
    membership is established once, up front, when the gym admin adds the
    member. This dependency just: verifies the caller's self-issued session
    token (see app/auth.py), and re-fetches the member row fresh from the DB
    (not just trusting stale claims in the token) so a status change (e.g.
    admin deactivates a member) takes effect on their very next request.
    """
    payload = verify_member_token(credentials.credentials)
    member_id = payload["sub"]

    res = (
        _supabase.table("members")
        .select("*")
        .eq("id", member_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Member account no longer exists.",
        )

    member = res.data[0]
    if member.get("status") != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been deactivated. Contact your gym.",
        )
    return member
