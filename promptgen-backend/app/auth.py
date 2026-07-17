import time
import uuid

from fastapi import HTTPException, status
from jose import jwt, JWTError

from app.config import settings

bearer_scheme_error_hint = (
    "Missing or invalid Authorization header. Log in again to get a new "
    "member session token."
)

# ── Member session tokens (self-issued, replaces Supabase Auth) ────────────
# History: this project used to verify Supabase Auth JWTs here (first via a
# hardcoded key guess, then via Supabase's JWKS endpoint — see git history
# for that saga). That entire mechanism depended on members signing up
# through Supabase Auth email/password on the frontend.
#
# That signup flow has been removed. The real, intended member-identity
# path is: a gym admin adds a member in gym-dashboard, which generates an
# 8-digit `login_code` on the `members` row. The member enters that code
# (see /member/login in main.py), which looks the row up directly — no
# Supabase Auth user is ever created for a member. So there is nothing to
# verify against Supabase's JWKS anymore.
#
# Instead, this app now signs its own short-lived-ish HS256 tokens keyed by
# MEMBER_SESSION_SECRET. The token's only job is "prove you're the member
# with this id" for subsequent API calls — it is NOT a Supabase Auth token
# and Supabase's own auth.uid()/RLS machinery does not know about it (any
# endpoint that used to rely on RLS + auth.uid() for a member now needs an
# explicit member_id check against the token instead — see membership.py).

_ALG = "HS256"
_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days — long-lived on purpose,
# since members log in rarely (once, from a link their gym sent) and
# there's no email/password to fall back on if the session dies.


def issue_member_token(member_id: str, gym_id: str | None) -> str:
    if not settings.member_session_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MEMBER_SESSION_SECRET is not set on this deployment — "
                   "member login is disabled until it's configured.",
        )
    now = int(time.time())
    payload = {
        "sub": member_id,
        "gym_id": gym_id,
        "iat": now,
        "exp": now + _TOKEN_TTL_SECONDS,
        # A random jti isn't checked against a revocation list anywhere
        # (no such table exists yet) — noted as a follow-up gap, same
        # category as the missing rate-limit on /member/login.
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.member_session_secret, algorithm=_ALG)


def verify_member_token(token: str) -> dict:
    if not settings.member_session_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MEMBER_SESSION_SECRET is not set on this deployment.",
        )
    try:
        payload = jwt.decode(token, settings.member_session_secret, algorithms=[_ALG])
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired session: {e}",
        )
    if not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session token.",
        )
    return payload

