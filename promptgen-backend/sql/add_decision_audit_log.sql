-- Run this once in Supabase → SQL Editor.
--
-- Data sink for decision_audit_engine.py (Engine 28). One row per real
-- decision this app makes (plan generation, intra-cycle adaptation, etc.)
-- — input/output content hashes for reproducibility ("did these two runs
-- get identical input and produce identical output"), NOT full payloads.
-- Deliberately no foreign-key cascade delete tied to a single plan row —
-- audit history should survive a plan being regenerated/expired, that's
-- the whole point of an audit log.
--
-- decision_audit_engine.record_decision() degrades gracefully (try/except,
-- logged not raised) if this table doesn't exist yet — same convention as
-- every other write-path in this app. Existing functionality is NOT
-- blocked by skipping this migration; you just won't get audit rows.

create table if not exists decision_audit_log (
  id                bigint generated always as identity primary key,
  member_id         uuid not null references members(id) on delete cascade,
  decision_type     text not null,
  source_engines    jsonb not null default '[]'::jsonb,
  input_hash        text not null,
  output_hash       text not null,
  kb_version        text,
  kb_content_hash   text,
  created_at        timestamptz not null default now()
);

create index if not exists decision_audit_log_member_idx
  on decision_audit_log (member_id, created_at desc);

create index if not exists decision_audit_log_type_idx
  on decision_audit_log (decision_type, created_at desc);
