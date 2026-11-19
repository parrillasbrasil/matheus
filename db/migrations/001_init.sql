-- Global Opportunity Radar — modelo de dados (spec) -----------------------------------
-- Regras de ouro:
--  * data_origin separa dado REAL, de TESTE (fixture) e DESCONHECIDO; o gatilho abaixo impede contaminação.
--  * Nada é apagado por falha de coleta: não há ON DELETE CASCADE a partir de fontes/documentos para oportunidades.

CREATE TABLE candidate_profiles (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  label       text NOT NULL UNIQUE,
  profile     jsonb NOT NULL,
  config_hash text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sources (
  id                 text PRIMARY KEY,
  name               text NOT NULL,
  kind               text NOT NULL,
  role               text NOT NULL DEFAULT 'opportunity' CHECK (role IN ('opportunity','program_rules')),
  origin             text NOT NULL CHECK (origin IN ('real','fixture')),
  enabled            boolean NOT NULL,
  url                text NOT NULL,
  country            text,
  language           text,
  interval_minutes   integer NOT NULL CHECK (interval_minutes > 0),
  rate_limit         jsonb NOT NULL,
  tos_status         text NOT NULL CHECK (tos_status IN ('allowed','pending_review','forbidden')),
  tos_note           text,
  tos_reviewed_on    date,
  tos_evidence_url   text,
  options            jsonb NOT NULL DEFAULT '{}',
  last_run_at        timestamptz,
  next_run_at        timestamptz,
  consecutive_failures integer NOT NULL DEFAULT 0,
  config_hash        text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE source_runs (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id       text NOT NULL REFERENCES sources(id),
  trigger         text NOT NULL DEFAULT 'schedule' CHECK (trigger IN ('schedule','manual','test')),
  status          text NOT NULL CHECK (status IN ('running','success','partial','failed','blocked_by_robots','blocked_by_tos','skipped')),
  started_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz,
  items_seen      integer NOT NULL DEFAULT 0,
  items_new       integer NOT NULL DEFAULT 0,
  items_changed   integer NOT NULL DEFAULT 0,
  items_unchanged integer NOT NULL DEFAULT 0,
  items_failed    integer NOT NULL DEFAULT 0,
  requests_made   integer NOT NULL DEFAULT 0,
  error           text,
  stats           jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX source_runs_source_started ON source_runs (source_id, started_at DESC);

CREATE TABLE documents (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id         text NOT NULL REFERENCES sources(id),
  canonical_url     text NOT NULL,
  external_id       text,
  content_type      text,
  title             text,
  text              text,
  text_chars        integer NOT NULL DEFAULT 0,
  truncated         boolean NOT NULL DEFAULT false,
  content_hash      text NOT NULL,
  status            text NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','needs_ocr','irrelevant','error')),
  processing_status text NOT NULL DEFAULT 'pending' CHECK (processing_status IN ('pending','processed','pending_llm','skipped','failed')),
  origin            text NOT NULL CHECK (origin IN ('real','fixture')),
  raw_metadata      jsonb NOT NULL DEFAULT '{}',
  first_seen_at     timestamptz NOT NULL DEFAULT now(),
  fetched_at        timestamptz NOT NULL DEFAULT now(),
  last_checked_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_id, canonical_url)
);
CREATE INDEX documents_hash ON documents (content_hash);

CREATE TABLE opportunities (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_key    text NOT NULL UNIQUE,
  source_id          text NOT NULL REFERENCES sources(id),
  document_id        uuid REFERENCES documents(id),
  canonical_url      text NOT NULL,
  is_official        boolean NOT NULL,
  data_origin        text NOT NULL CHECK (data_origin IN ('real','fixture','unknown')),
  title_original     text,
  title_pt           text,
  issuer             text,
  country            text,
  city_or_region     text,
  benefit_types      text[] NOT NULL DEFAULT '{}',
  category           text,
  aid_type           text,
  benefit_summary_pt text,
  value_amount       numeric,
  value_currency     text,
  value_gross_net    text,
  value_period       text,
  deadline_at        timestamptz,
  deadline_tz        text,
  deadline_tz_assumed boolean NOT NULL DEFAULT false,
  deadline_confirmed boolean NOT NULL DEFAULT false,
  status             text NOT NULL CHECK (status IN ('open','closed','unknown')),
  eligibility_status text NOT NULL CHECK (eligibility_status IN ('eligible','needs_confirmation','ineligible')),
  relocation_condition text NOT NULL CHECK (relocation_condition IN ('none','before_apply','after_award')),
  eligibility_summary text,
  requirements       jsonb NOT NULL DEFAULT '[]',
  obligations        jsonb NOT NULL DEFAULT '[]',
  not_covered_expenses jsonb NOT NULL DEFAULT '[]',
  extraction         jsonb NOT NULL,
  extraction_origin  text NOT NULL CHECK (extraction_origin IN ('llm','deterministic_feed','manual')),
  audit_status       text NOT NULL DEFAULT 'not_requested' CHECK (audit_status IN ('not_requested','confirmed','disputed','unavailable')),
  last_verified_at   timestamptz NOT NULL,
  content_hash       text NOT NULL,
  current_version_id uuid,
  current_score      numeric,
  first_seen_at      timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX opportunities_elig ON opportunities (eligibility_status);
CREATE INDEX opportunities_status ON opportunities (status);
CREATE INDEX opportunities_origin ON opportunities (data_origin);
CREATE INDEX opportunities_score ON opportunities (current_score DESC NULLS LAST);
CREATE INDEX opportunities_deadline ON opportunities (deadline_at);

CREATE TABLE opportunity_versions (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id uuid NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
  version_no     integer NOT NULL,
  content_hash   text NOT NULL,
  snapshot       jsonb NOT NULL,
  text_excerpt   text,
  change_summary jsonb NOT NULL DEFAULT '{}',
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (opportunity_id, version_no)
);

CREATE TABLE evidence_spans (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id uuid NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
  version_id     uuid REFERENCES opportunity_versions(id) ON DELETE CASCADE,
  document_id    uuid REFERENCES documents(id),
  field          text NOT NULL,
  quote          text NOT NULL,
  start_char     integer,
  end_char       integer,
  verified       boolean NOT NULL,
  source_url     text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX evidence_opp ON evidence_spans (opportunity_id, version_id);

CREATE TABLE eligibility_checks (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id       uuid NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
  version_id           uuid REFERENCES opportunity_versions(id) ON DELETE CASCADE,
  requirement_id       text NOT NULL,
  kind                 text NOT NULL,
  result               text NOT NULL CHECK (result IN ('pass','fail','unknown','info')),
  reason_pt            text NOT NULL,
  quote                text,
  source_url           text,
  origin               text NOT NULL CHECK (origin IN ('extracted','program_rule','deterministic')),
  checked_by           text NOT NULL DEFAULT 'deterministic' CHECK (checked_by IN ('deterministic','claude_audit')),
  fixable_by_relocation boolean NOT NULL DEFAULT false,
  created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX checks_opp ON eligibility_checks (opportunity_id, version_id);

CREATE TABLE scores (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id uuid NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
  version_id     uuid REFERENCES opportunity_versions(id) ON DELETE CASCADE,
  total          numeric NOT NULL,
  components     jsonb NOT NULL,
  explanation    jsonb NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE alerts (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id     uuid NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
  version_id         uuid REFERENCES opportunity_versions(id) ON DELETE CASCADE,
  kind               text NOT NULL CHECK (kind IN ('new','changed','urgent')),
  channel            text NOT NULL CHECK (channel IN ('telegram','smtp')),
  dedupe_key         text NOT NULL UNIQUE,
  status             text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed','suppressed')),
  payload            jsonb NOT NULL DEFAULT '{}',
  attempts           integer NOT NULL DEFAULT 0,
  last_error         text,
  suppression_reason text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  sent_at            timestamptz
);

-- O sistema NUNCA envia candidaturas: 'submitted_by_human' só é marcado por uma pessoa.
CREATE TABLE applications (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id uuid NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
  status         text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','awaiting_human_approval','approved_by_human','submitted_by_human','withdrawn')),
  notes          text,
  approved_by    text,
  approved_at    timestamptz,
  submitted_at   timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_events (
  id          bigserial PRIMARY KEY,
  at          timestamptz NOT NULL DEFAULT now(),
  actor       text NOT NULL,
  event       text NOT NULL,
  entity_type text,
  entity_id   text,
  details     jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_at ON audit_events (at DESC);

CREATE TABLE agent_usage (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  at             timestamptz NOT NULL DEFAULT now(),
  provider       text NOT NULL CHECK (provider IN ('openai','anthropic')),
  model          text NOT NULL,
  purpose        text NOT NULL CHECK (purpose IN ('extraction','audit')),
  document_id    uuid REFERENCES documents(id),
  opportunity_id uuid REFERENCES opportunities(id),
  input_tokens   integer NOT NULL DEFAULT 0,
  output_tokens  integer NOT NULL DEFAULT 0,
  cost_usd       numeric,
  cost_known     boolean NOT NULL DEFAULT false,
  cached         boolean NOT NULL DEFAULT false,
  status         text NOT NULL CHECK (status IN ('ok','error','budget_blocked')),
  error          text
);
CREATE INDEX agent_usage_at ON agent_usage (at DESC);

-- Dado de teste nunca pode virar 'real' (nem o contrário).
CREATE FUNCTION enforce_data_origin() RETURNS trigger AS $$
DECLARE src_origin text;
BEGIN
  SELECT origin INTO src_origin FROM sources WHERE id = NEW.source_id;
  IF src_origin = 'fixture' AND NEW.data_origin <> 'fixture' THEN
    RAISE EXCEPTION 'oportunidade de fonte de teste (fixture) não pode ter data_origin=%', NEW.data_origin;
  END IF;
  IF src_origin = 'real' AND NEW.data_origin = 'fixture' THEN
    RAISE EXCEPTION 'oportunidade de fonte real não pode ter data_origin=fixture';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER opportunities_origin_guard BEFORE INSERT OR UPDATE ON opportunities
  FOR EACH ROW EXECUTE FUNCTION enforce_data_origin();
