# DEPLOY_READINESS.md

Read this before deploying. Audited this session (separate from
engine-build/wiring work in HANDOFF.md — this is "is the app safe and
correct to put on the internet," not "how many engines exist").

## 🔴 Must do before deploy (you, not me — needs real credentials/dashboard access)

1. **Rotate every secret in `.env` if this zip, or any earlier zip from
   this conversation, was ever shared or uploaded anywhere else.**
   `.env` contains a real Supabase `service_role` key (full DB access,
   bypasses RLS), a real `SUPABASE_JWT_SECRET`, and a real
   `GEMINI_API_KEY`. These are live credentials, not placeholders. I've
   been bundling this file into every zip I handed back this session by
   default — that stops now (see below), but I can't un-send what's
   already been sent. If in doubt, rotate: Supabase dashboard → Settings →
   API → regenerate `service_role` + JWT secret; Google AI Studio → revoke
   and reissue the Gemini key. Then update Render's env vars (see #2) with
   the new values — `render.yaml` uses `sync: false` for all of these,
   meaning Render expects them typed into its dashboard, not read from a
   committed/zipped file.

2. **Enter all `render.yaml` env vars in Render's dashboard**:
   `SUPABASE_URL`, `SUPABASE_JWT_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`,
   `DEMO_GYM_ID`, `GEMINI_API_KEY`, `FRONTEND_ORIGIN`, `DEV_TEST_KEY`,
   `MEMBER_FRONTEND_URL`, `MEMBER_SESSION_SECRET`. For `DEV_TEST_KEY`:
   recommend leaving this **blank** on the production Render service
   unless the dev-console integration is actually live — the endpoint
   fails closed (503) when unset, which is the safe default; only set it
   if you specifically need `/generate/test` reachable in prod.

3. **Run the pending SQL migrations against the real Supabase project** —
   I can't do this from here (no real DB credentials, and shouldn't have
   them). Files that need to actually be applied, not just present in the
   repo: `sql/add_readiness_checkins.sql` (session 4/5 found the
   `readiness_checkins` table referenced by `readiness_engine.py` never
   existed — every call was silently returning its no-data default until
   this is run), `sql/create_feedback_table.sql`,
   `sql/create_reassessment_tables.sql`, `sql/patch_real_feedback_tables.sql`,
   `sql/scale_indexes.sql`, `migrations/phase2_add_password_hash.sql`.
   Confirm each has actually been run — presence in the repo doesn't mean
   applied.

## 🟡 Found and fixed this session (no action needed from you)

4. **CORS was wide open** (`Access-Control-Allow-Origin: *` hardcoded in
   `main.py`'s `ManualCORS` middleware), even though a real allow-list
   already existed in `config.py` (`FRONTEND_ORIGIN` → `settings.
   frontend_origins`) and was simply never consulted. Fixed: now echoes
   back the request's `Origin` header only if it's in the configured
   allow-list, omits CORS headers entirely otherwise. Tested with an
   allowed origin, a disallowed one, and no-origin (server-to-server) —
   all three behave correctly. Lower severity than it could have been —
   member auth is Bearer-token (see `membership.py`'s `HTTPBearer`), not
   cookie-based, so this was never a direct credential-theft CORS hole —
   but it ignored a real, already-built restriction for no reason.

5. **Regression suite (`tests/regression/`) was failing 4/4** against its
   committed baselines. Root-caused before touching anything: reverted
   `exercise_database.py` to its pre-session-1 version to isolate the
   cause, confirmed the failures trace entirely to that deliberate swap
   (session 1) to the new, KB-sourced exercise database — which is now
   load-bearing (3+ later-session engines import functions, like
   `get_substitutes_for_exercise`, that only exist in the new version;
   reverting broke the app outright rather than just changing behavior).
   Manually reviewed the diffs before recapturing anything: the new
   behavior is a real improvement, not a hidden regression — the richer
   substitute pool lets the validator actually *repair* duplicate-
   movement-pattern issues that the old, thinner pool could only warn
   about and leave unfixed. Recaptured baselines against current, correct
   behavior; reran — all 4 pass clean now. Also ran the 4 test files
   under `app/tests_new/` via pytest — 45/45 pass.

## 🟢 Confirmed already correct, no action needed

6. `render.yaml` (present in both repo root and `promptgen-backend/`,
   identical) is a real, correct Render config — `rootDir`, build/start
   commands, and env var list all check out.
7. `requirements.txt` has every package actually needed to import and run
   `main.py` (verified by literally installing each one fresh and
   re-running the import until it succeeded) — fastapi, uvicorn, httpx,
   python-jose, pydantic/pydantic-settings, python-dotenv, jinja2,
   python-multipart, google-genai, supabase, bcrypt.
8. `/generate/test` (the dev-console debug endpoint) fails closed by
   design — 503 if `DEV_TEST_KEY` unset, 401 on a wrong key. Good default,
   just make the deliberate call on whether to set the key in prod (#2).
9. `.env` / `*.env` are correctly gitignored — won't hit a public git repo
   even though the file itself is still a live local risk (see #1).

## ⚪ Found, not a security issue, just housekeeping debt

10. **"Phase 2: member password auth" is documented as if it's a working
    feature (`.env.example`, `config.py` comments reference
    `POST /member/set-password`) but that endpoint does not exist
    anywhere in `main.py`.** `bcrypt` sits in `requirements.txt` unused,
    and `migrations/phase2_add_password_hash.sql` adds a column nothing
    currently writes to or reads from. Not a bug — members authenticate
    entirely via the 8-digit login code (`membership.py`: "there is no
    member password"). But the comments overstate what's actually built;
    if password auth isn't actually planned, worth deleting the dead
    migration/comments/dependency in a cleanup pass. If it IS planned,
    it's an unbuilt feature, not a "phase 2 already shipped" one.
11. `.env.example`'s `SUPABASE_URL` value is the real project URL (not a
    placeholder like the other fields in the same file) — low risk on its
    own (a URL isn't a secret, and combined with rotated keys per #1 it's
    moot), but inconsistent with the rest of the file's placeholder
    pattern.

## What I did NOT audit this session (out of scope / needs different access)

- Actual production load testing, rate limiting, uptime/monitoring setup.
- Supabase Row Level Security (RLS) policies — the app used to rely on
  Supabase Auth + RLS for members (per `main.py`'s own comments) and has
  since moved to app-level Bearer auth instead; whether RLS policies were
  ever updated/removed to match, or whether they're now redundant-but-
  harmless vs actually conflicting, needs checking directly in the
  Supabase dashboard, which I don't have access to.
- Frontend deploy (Vercel, per `MEMBER_FRONTEND_URL`) — appears to already
  be set up and separate from this backend's Render deploy; not touched
  or verified this session.
- The 27 engines that are built but not wired into any endpoint (see
  HANDOFF.md) are inert, unreachable code — not a safety issue, just
  unused. Deploying with them present is fine; they simply do nothing
  until wired.
