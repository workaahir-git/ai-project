-- Multi-tenant scale patch. Idempotent, safe to run anytime.
-- Every gym-scoped query in gyms.py / links.py / membership.py filters on
-- these columns (`.eq("gym_id", ...)` / `.eq("slug", ...)`). Without an
-- index each one is a full table scan — invisible at a handful of gyms,
-- real latency once you're past a few hundred members total.

create index if not exists members_gym_id_idx  on members(gym_id);
create index if not exists admins_gym_id_idx   on admins(gym_id);
create index if not exists payments_gym_id_idx on payments(gym_id);

-- gym_scope.py's resolve_gym_id() looks up by slug on every gym-scoped
-- login/join. schema.sql already adds a UNIQUE constraint on gyms.slug,
-- which Postgres backs with an index automatically — no separate index
-- needed there, noted here just so it's not mistaken for an oversight.
