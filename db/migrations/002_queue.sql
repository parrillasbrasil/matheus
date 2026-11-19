-- Fila de jobs em PostgreSQL (SKIP LOCKED), cache de LLM e heartbeat dos serviços.

CREATE TABLE jobs (
  id          bigserial PRIMARY KEY,
  kind        text NOT NULL,
  payload     jsonb NOT NULL DEFAULT '{}',
  status      text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','dead')),  -- 'dead' = DLQ
  attempts    integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 5,
  run_at      timestamptz NOT NULL DEFAULT now(),
  locked_at   timestamptz,
  locked_by   text,
  last_error  text,
  dedupe_key  text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX jobs_claim ON jobs (run_at) WHERE status = 'queued';
CREATE UNIQUE INDEX jobs_dedupe ON jobs (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued','running');

-- Evita reprocessar (e pagar de novo) o mesmo documento com o mesmo prompt/modelo.
CREATE TABLE llm_cache (
  cache_key     text PRIMARY KEY,
  provider      text NOT NULL,
  model         text NOT NULL,
  response      jsonb NOT NULL,
  input_tokens  integer NOT NULL DEFAULT 0,
  output_tokens integer NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE service_heartbeats (
  service   text PRIMARY KEY,
  last_seen timestamptz NOT NULL,
  info      jsonb NOT NULL DEFAULT '{}'
);
