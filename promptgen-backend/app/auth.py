from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError

from app.config import settings

bearer_scheme = HTTPBearer()

# Fix (flagged as a known, unfixed bug in admin-dashboard-backend's HANDOFF.md):
# this used to verify with a hardcoded ES256 public key, but Supabase's
# legacy JWT signing (the "legacy JWT secret" shown in Project Settings ->
# API) issues HS256 tokens, not ES256. Verifying with the wrong algorithm
# either throws on every request or silently only "worked" for a token
# shape that never matched what Supabase actually issues. Switched to
# HS256 with the shared secret already present in .env
# (SUPABASE_JWT_SECRET) - it was already read into settings, just unused.
#
# If this project later migrates to Supabase's newer per-project signing
# keys (asymmetric, ES256/RS256 with a JWKS endpoint), this needs to change
# again to fetch and cache the JWKS instead of a static key. Not done here
# since the current project is still on the legacy HS256 shared secret.


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {e}",
        )

    return payload
