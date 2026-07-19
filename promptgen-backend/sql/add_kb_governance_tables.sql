-- Run this once in Supabase → SQL Editor.
--
-- Two tables for research_integration_engine.py (Engine 38) and
-- continuous_improvement_engine.py (Engine 37) — both real proposal-
-- tracking sinks for changes to knowledge_base.json, formalizing the
-- same process sessions 18/19/21/22 of this app's history already did by
-- hand. Neither table has a member_id — these are app/KB-level records,
-- not per-member data, so no FK to members() and no per-member RLS
-- concern here.
--
-- Both engines' write functions degrade gracefully (try/except) if these
-- tables don't exist yet — same convention as decision_audit_log.

create table if not exists research_integration_log (
  id                      bigint generated always as identity primary key,
  publication_id          text not null,
  source_type             text not null,
  evidence_grade          text not null check (evidence_grade in ('A','B','C','D')),
  affected_engines        jsonb not null default '[]'::jsonb,
  integration_status      text not null default 'pending',
  reviewer                text,
  implementation_version  text,
  created_at              timestamptz not null default now()
);

create table if not exists improvement_proposals (
  id                          bigint generated always as identity primary key,
  source                      text not null,
  affected_engines            jsonb not null default '[]'::jsonb,
  improvement_type            text not null,
  evidence_level               text not null,
  description                 text,
  validation_status           text not null default 'proposed',
  needs_consistency_review    boolean not null default false,
  kb_content_hash_at_proposal text,
  implementation_version      text,
  created_at                  timestamptz not null default now()
);
