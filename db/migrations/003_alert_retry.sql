-- Reenvio de alertas com backoff: quando tentar de novo.
ALTER TABLE alerts ADD COLUMN next_attempt_at timestamptz;
CREATE INDEX alerts_pending ON alerts (next_attempt_at) WHERE status = 'pending';
