-- Run this once in Supabase → SQL Editor.
--
-- Data source for readiness_engine.py (Engine 9). readiness_engine.py's own
-- module docstring has referenced this file since it was written, but the
-- table was never actually created in any previous session — get_readiness()
-- has been running against a table that doesn't exist and silently
-- returning its "No readiness check-in submitted" default every time.
--
-- One row per training session: a 1-5 pre-session self-report ("how do you
-- feel today?"), optionally with free-text notes for pain/illness keyword
-- detection (same _contains_pain_language / _contains_illness_language
-- checks used elsewhere in this app). Deliberately ONE number, not five
-- separate sleep/soreness/stress/recovery sub-scores — see
-- readiness_engine.py's docstring for why fabricating those would be worse
-- than not having them.

create table if not exists readiness_checkins (
  id            bigint generated always as identity primary key,
  member_id     uuid not null references members(id) on delete cascade,
  cycle_number  int not null,
  day_index     int not null,
  rating        int not null check (rating between 1 and 5),
  notes         text,
  created_at    timestamptz not null default now(),
  constraint readiness_checkins_member_cycle_day_key
    unique (member_id, cycle_number, day_index)
);

create index if not exists readiness_checkins_member_cycle_idx
  on readiness_checkins(member_id, cycle_number);

-- NOTE: adjust `references members(id)` above if your members table has a
-- different name/primary key column — same note as create_feedback_table.sql.
