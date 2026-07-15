"""
Multi-gym scoping skeleton.

This app has been single-tenant since launch — every member gets joined to
one hardcoded `settings.demo_gym_id` via the `join_gym_if_capacity` RPC
(see app/membership.py, pre-existing). The `gyms` table already has a
unique `slug` column (added in admin-dashboard-backend/schema.sql), and the
gym-admin / dev-console dashboards already generate access links shaped
like `{MEMBER_APP_BASE_URL}/member/login?gym={slug}` — but nothing on this
side ever reads that `gym` value. This module is the missing piece.

Design: resolve a `gym` slug (from a header or query param, see
membership.py) to a gym row, with the existing demo gym as the fallback so
every route that depended on single-tenant behavior keeps working exactly
as before when no slug is supplied. This is deliberately additive — it does
not touch the `join_gym_if_capacity` RPC, which already takes `p_gym_id` as
a parameter and therefore already supports any gym, not just the demo one.

Known gap left for a follow-up pass (not done here): no per-gym caching /
invalidation. `lru_cache` is fine at current scale (few gyms, low QPS) but
should move to a short-TTL cache once gym count grows, same caveat
`knowledge_retriever.py` already calls out for its own lru_cache usage.
"""
from functools import lru_cache

from app.config import settings
from app.db import supabase


class GymLookupError(Exception):
    """Raised when a `gym` slug is supplied but doesn't resolve to a real,
    active gym. Callers should turn this into a 404, not fall back silently
    — silently falling back to the demo gym on a typo'd slug would put a
    member in the wrong gym's roster without anyone noticing."""


@lru_cache(maxsize=256)
def _lookup_gym_by_slug(slug: str) -> dict | None:
    res = (
        supabase.table("gyms")
        .select("id, slug, name, status")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def resolve_gym_id(slug: str | None) -> str:
    """
    Returns the gym_id to scope a member/join operation to.

    - No slug supplied  -> settings.demo_gym_id (unchanged legacy behavior).
    - Slug supplied      -> looked up in `gyms`; must exist and not be
                             suspended, or this raises GymLookupError.
    """
    if not slug:
        return settings.demo_gym_id

    gym = _lookup_gym_by_slug(slug.strip().lower())
    if gym is None:
        raise GymLookupError(f"No gym found for slug '{slug}'.")
    if gym.get("status") == "suspended":
        raise GymLookupError(f"Gym '{slug}' is currently suspended.")
    return gym["id"]


def clear_cache() -> None:
    """Call after any gym status/slug change made elsewhere (e.g. if this
    process also handles admin actions) — not currently wired to anything
    since gym admin/dev actions happen in the other two repos' processes,
    left here for when this becomes a shared package instead of 3 repos."""
    _lookup_gym_by_slug.cache_clear()
