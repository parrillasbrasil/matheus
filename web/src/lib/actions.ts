// Escritas do painel. Todas auditadas (audit_events). O painel NUNCA envia candidatura:
// só registra o que uma pessoa decidiu (approved_by_human / submitted_by_human).
import type { Pool } from "pg";
import { isUuid } from "./queries";

export type Result<T = object, R extends string = string> = ({ ok: true } & T) | { ok: false; reason: R };

async function audit(c: { query: Pool["query"] }, actor: string, event: string, entityType: string | null, entityId: string | null, details: object = {}) {
  await c.query("INSERT INTO audit_events (actor, event, entity_type, entity_id, details) VALUES ($1,$2,$3,$4,$5)", [actor, event, entityType, entityId, JSON.stringify(details)]);
}

export type ScanReason = "unknown_source" | "disabled" | "fixture_source" | "tos_not_allowed" | "already_queued";

/** Pedido de execução manual. Mesmas regras do agendador: fonte habilitada, real e com ToS 'allowed'. */
export async function enqueueScan(pool: Pool, sourceId: string, actor: string, now: Date): Promise<Result<{ jobId: number }, ScanReason>> {
  const src = (await pool.query("SELECT enabled, origin, tos_status FROM sources WHERE id=$1", [sourceId])).rows[0];
  if (!src) return { ok: false, reason: "unknown_source" };
  if (!src.enabled) return { ok: false, reason: "disabled" };
  if (src.origin === "fixture") return { ok: false, reason: "fixture_source" };
  if (src.tos_status !== "allowed") return { ok: false, reason: "tos_not_allowed" };
  const c = await pool.connect();
  try {
    await c.query("BEGIN");
    // max_attempts: padrão da tabela (5), igual a queue.max_attempts do config.yaml.
    const row = (await c.query(
      `INSERT INTO jobs (kind, payload, run_at, dedupe_key, created_at) VALUES ('scan_source',$1,$2,$3,$2)
       ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued','running') DO NOTHING RETURNING id`,
      [JSON.stringify({ source_id: sourceId, trigger: "manual" }), now.toISOString(), `scan:${sourceId}`])).rows[0];
    if (!row) { await c.query("ROLLBACK"); return { ok: false, reason: "already_queued" }; }
    await audit(c, actor, "scan_requested", "source", sourceId, { job_id: Number(row.id) });
    await c.query("COMMIT");
    return { ok: true, jobId: Number(row.id) };
  } catch (e) {
    await c.query("ROLLBACK").catch(() => undefined);
    throw e;
  } finally {
    c.release();
  }
}

export async function requeueDeadJob(pool: Pool, jobId: number, actor: string, now: Date): Promise<Result<object, "not_dead" | "already_queued">> {
  if (!Number.isSafeInteger(jobId) || jobId < 1) return { ok: false, reason: "not_dead" };
  try {
    const r = await pool.query("UPDATE jobs SET status='queued', attempts=0, run_at=$2, last_error=NULL, finished_at=NULL WHERE id=$1 AND status='dead'", [jobId, now.toISOString()]);
    if (r.rowCount !== 1) return { ok: false, reason: "not_dead" };
  } catch (e) {
    if ((e as { code?: string }).code === "23505") return { ok: false, reason: "already_queued" };   // outro job com a mesma dedupe_key já está na fila
    throw e;
  }
  await audit(pool, actor, "job_requeued", "job", String(jobId));
  return { ok: true };
}

// ---- candidaturas -----------------------------------------------------------------------------
export const APPLICATION_STATUSES = ["draft", "awaiting_human_approval", "approved_by_human", "submitted_by_human", "withdrawn"] as const;
export type ApplicationStatus = (typeof APPLICATION_STATUSES)[number];
const NEXT: Record<ApplicationStatus, readonly ApplicationStatus[]> = {
  draft: ["awaiting_human_approval", "withdrawn"],
  awaiting_human_approval: ["approved_by_human", "withdrawn"],
  approved_by_human: ["submitted_by_human", "withdrawn"],
  submitted_by_human: [],
  withdrawn: [],
};

export async function createApplicationDraft(pool: Pool, opportunityId: string, actor: string, _now: Date): Promise<Result<{ id: string }, "unknown_opportunity" | "already_exists">> {
  if (!isUuid(opportunityId) || !(await pool.query("SELECT 1 FROM opportunities WHERE id=$1", [opportunityId])).rowCount) return { ok: false, reason: "unknown_opportunity" };
  const c = await pool.connect();
  try {
    await c.query("BEGIN");
    await c.query("SELECT pg_advisory_xact_lock(hashtext($1))", [`application:${opportunityId}`]);   // evita dois rascunhos simultâneos
    if ((await c.query("SELECT 1 FROM applications WHERE opportunity_id=$1 AND status <> 'withdrawn'", [opportunityId])).rowCount) { await c.query("ROLLBACK"); return { ok: false, reason: "already_exists" }; }
    const id = (await c.query("INSERT INTO applications (opportunity_id) VALUES ($1) RETURNING id", [opportunityId])).rows[0].id as string;
    await audit(c, actor, "application_created", "application", id, { opportunity_id: opportunityId });
    await c.query("COMMIT");
    return { ok: true, id };
  } catch (e) {
    await c.query("ROLLBACK").catch(() => undefined);
    throw e;
  } finally {
    c.release();
  }
}

export async function transitionApplication(pool: Pool, id: string, to: ApplicationStatus, actor: string, now: Date): Promise<Result<object, "unknown_application" | "invalid_transition">> {
  if (!isUuid(id)) return { ok: false, reason: "unknown_application" };
  const cur = (await pool.query("SELECT status FROM applications WHERE id=$1", [id])).rows[0];
  if (!cur) return { ok: false, reason: "unknown_application" };
  if (!NEXT[cur.status as ApplicationStatus]?.includes(to)) return { ok: false, reason: "invalid_transition" };
  // A condição `status = $from` impede que duas pessoas apliquem a mesma transição ao mesmo tempo.
  const r = await pool.query(
    `UPDATE applications SET status=$2, updated_at=$4,
       approved_by = CASE WHEN $2='approved_by_human' THEN $5 ELSE approved_by END,
       approved_at = CASE WHEN $2='approved_by_human' THEN $4 ELSE approved_at END,
       submitted_at = CASE WHEN $2='submitted_by_human' THEN $4 ELSE submitted_at END
     WHERE id=$1 AND status=$3`, [id, to, cur.status, now.toISOString(), actor]);
  if (r.rowCount !== 1) return { ok: false, reason: "invalid_transition" };
  await audit(pool, actor, `application_${to}`, "application", id, { from: cur.status });
  return { ok: true };
}
