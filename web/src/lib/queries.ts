// Consultas de leitura do painel. Tudo parametrizado; nenhuma escrita aqui (ver actions.ts).
import type { Pool } from "pg";
import { buildOpportunityQuery, DEFAULT_PAGE_SIZE, LIST_COLUMNS, type Filters } from "./filters";

export type Row = Record<string, any>;   // eslint-disable-line @typescript-eslint/no-explicit-any
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isUuid = (v: string) => UUID.test(v);

export async function listOpportunities(pool: Pool, f: Filters): Promise<{ rows: Row[]; hasNext: boolean }> {
  const { text, values } = buildOpportunityQuery(f);
  const { rows } = await pool.query(text, values);
  return { rows: rows.slice(0, DEFAULT_PAGE_SIZE), hasNext: rows.length > DEFAULT_PAGE_SIZE };
}

export async function getOpportunity(pool: Pool, id: string) {
  if (!isUuid(id)) return null;
  const opp = (await pool.query(`SELECT o.*, s.name AS source_name, s.tos_status, s.origin AS source_origin FROM opportunities o JOIN sources s ON s.id = o.source_id WHERE o.id = $1`, [id])).rows[0];
  if (!opp) return null;
  const vid = opp.current_version_id;
  const [evidence, checks, score, versions, alerts, applications] = await Promise.all([
    pool.query(`SELECT field, quote, verified, source_url, start_char, end_char FROM evidence_spans WHERE opportunity_id=$1 AND version_id IS NOT DISTINCT FROM $2 ORDER BY verified DESC, field, id`, [id, vid]),
    pool.query(`SELECT requirement_id, kind, result, reason_pt, quote, origin, checked_by, fixable_by_relocation FROM eligibility_checks WHERE opportunity_id=$1 AND version_id IS NOT DISTINCT FROM $2 ORDER BY CASE result WHEN 'fail' THEN 0 WHEN 'unknown' THEN 1 WHEN 'pass' THEN 2 ELSE 3 END, requirement_id`, [id, vid]),
    pool.query(`SELECT total, components, explanation, created_at FROM scores WHERE opportunity_id=$1 ORDER BY created_at DESC, id DESC LIMIT 1`, [id]),
    pool.query(`SELECT id, version_no, content_hash, change_summary, created_at FROM opportunity_versions WHERE opportunity_id=$1 ORDER BY version_no DESC`, [id]),
    pool.query(`SELECT kind, channel, status, suppression_reason, attempts, created_at, sent_at FROM alerts WHERE opportunity_id=$1 ORDER BY created_at DESC LIMIT 20`, [id]),
    pool.query(`SELECT id, status, notes, approved_by, approved_at, submitted_at, created_at FROM applications WHERE opportunity_id=$1 ORDER BY created_at DESC`, [id]),
  ]);
  return { opportunity: opp as Row, evidence: evidence.rows as Row[], checks: checks.rows as Row[], score: (score.rows[0] ?? null) as Row | null,
    versions: versions.rows as Row[], alerts: alerts.rows as Row[], applications: applications.rows as Row[] };
}

const LANE_KEYS = ["eligible", "needs_confirmation", "ineligible"] as const;
type Lane = Record<(typeof LANE_KEYS)[number], number>;

export async function overview(pool: Pool, now: Date) {
  const counts = await pool.query(`SELECT data_origin, eligibility_status, count(*)::int AS n FROM opportunities GROUP BY 1,2`);
  const empty = (): Lane => ({ eligible: 0, needs_confirmation: 0, ineligible: 0 });
  const lanes: Record<"real" | "fixture" | "unknown", Lane> = { real: empty(), fixture: empty(), unknown: empty() };
  for (const r of counts.rows) lanes[r.data_origin as keyof typeof lanes][r.eligibility_status as keyof Lane] = r.n;
  // Só abertas, com prazo CONFIRMADO ainda no futuro e não inelegíveis: o mesmo critério do alerta de urgência.
  const closing = await pool.query(
    `SELECT ${LIST_COLUMNS} FROM opportunities o
      WHERE o.status='open' AND o.deadline_confirmed AND o.deadline_at > $1 AND o.deadline_at <= $1::timestamptz + interval '14 days' AND o.eligibility_status <> 'ineligible'
      ORDER BY o.deadline_at ASC LIMIT 10`, [now.toISOString()]);
  const [health, jobs, srcs] = await Promise.all([
    workerHealth(pool, now),
    pool.query(`SELECT status, count(*)::int AS n FROM jobs GROUP BY 1`),
    pool.query(`SELECT count(*) FILTER (WHERE enabled AND consecutive_failures > 0)::int AS failing,
                       count(*) FILTER (WHERE tos_status='pending_review')::int AS pending_tos,
                       count(*) FILTER (WHERE enabled AND tos_status='allowed')::int AS active FROM sources WHERE origin='real'`),
  ]);
  const dead = jobs.rows.find((r) => r.status === "dead")?.n ?? 0;
  return { lanes, closingSoon: closing.rows as Row[], worker: health, deadJobs: dead as number, sources: srcs.rows[0] as { failing: number; pending_tos: number; active: number } };
}

export const HEARTBEAT_STALE_SECONDS = 300;

export async function workerHealth(pool: Pool, now: Date): Promise<{ state: "ok" | "stale" | "never"; last_seen: string | null; age_seconds: number | null }> {
  const r = (await pool.query(`SELECT last_seen FROM service_heartbeats WHERE service='worker'`)).rows[0];
  if (!r) return { state: "never", last_seen: null, age_seconds: null };
  const age = Math.max(0, Math.round((now.getTime() - new Date(r.last_seen).getTime()) / 1000));
  return { state: age <= HEARTBEAT_STALE_SECONDS ? "ok" : "stale", last_seen: new Date(r.last_seen).toISOString(), age_seconds: age };
}

export async function listSources(pool: Pool): Promise<Row[]> {
  return (await pool.query(
    `SELECT s.id, s.name, s.kind, s.role, s.origin, s.enabled, s.url, s.country, s.tos_status, s.tos_note, s.tos_reviewed_on, s.tos_evidence_url,
            s.interval_minutes, s.last_run_at, s.next_run_at, s.consecutive_failures,
            r.status AS last_run_status, r.error AS last_run_error, r.items_seen AS last_run_items, r.started_at AS last_run_started_at,
            (SELECT count(*)::int FROM opportunities o WHERE o.source_id = s.id) AS opportunities
       FROM sources s
       LEFT JOIN LATERAL (SELECT status, error, items_seen, started_at FROM source_runs WHERE source_id = s.id ORDER BY started_at DESC LIMIT 1) r ON true
      ORDER BY (s.origin = 'real') DESC, s.enabled DESC, s.id`)).rows;
}

export async function listRecentRuns(pool: Pool, limit = 25): Promise<Row[]> {
  return (await pool.query(`SELECT id, source_id, trigger, status, started_at, finished_at, items_seen, items_new, items_changed, items_unchanged, items_failed, error FROM source_runs ORDER BY started_at DESC LIMIT $1`, [limit])).rows;
}

export async function listJobs(pool: Pool) {
  const stats = { queued: 0, running: 0, done: 0, dead: 0 };
  for (const r of (await pool.query(`SELECT status, count(*)::int AS n FROM jobs GROUP BY 1`)).rows) stats[r.status as keyof typeof stats] = r.n;
  const [dead, recent] = await Promise.all([
    pool.query(`SELECT id, kind, payload, attempts, max_attempts, last_error, finished_at FROM jobs WHERE status='dead' ORDER BY finished_at DESC NULLS LAST, id DESC LIMIT 50`),
    pool.query(`SELECT id, kind, payload, status, attempts, run_at, last_error FROM jobs WHERE status IN ('queued','running') ORDER BY run_at LIMIT 50`),
  ]);
  return { stats, dead: dead.rows as Row[], active: recent.rows as Row[] };
}

export async function usageSummary(pool: Pool, now: Date) {
  const agg = (where: string) => `SELECT coalesce(sum(input_tokens),0)::bigint AS input_tokens, coalesce(sum(output_tokens),0)::bigint AS output_tokens, count(*)::int AS calls,
      count(*) FILTER (WHERE NOT cost_known AND NOT cached)::int AS unknown_cost_calls, coalesce(sum(cost_usd) FILTER (WHERE cost_known),0) AS known_cost_usd,
      count(*) FILTER (WHERE cached)::int AS cached_calls FROM agent_usage WHERE ${where}`;
  const day = now.toISOString().slice(0, 10);
  const [today, last30, byModel] = await Promise.all([
    pool.query(`${agg("at >= $1::date AND at < $1::date + 1 AND status='ok'")}`, [day]),
    pool.query(`${agg("at >= $1::timestamptz - interval '30 days' AND status='ok'")}`, [now.toISOString()]),
    pool.query(`SELECT provider, model, purpose, count(*)::int AS calls, sum(input_tokens)::bigint AS input_tokens, sum(output_tokens)::bigint AS output_tokens,
                       count(*) FILTER (WHERE NOT cost_known AND NOT cached)::int AS unknown_cost_calls, coalesce(sum(cost_usd) FILTER (WHERE cost_known),0) AS known_cost_usd
                  FROM agent_usage WHERE at >= $1::timestamptz - interval '30 days' AND status='ok' GROUP BY 1,2,3 ORDER BY 1,2,3`, [now.toISOString()]),
  ]);
  const num = (r: Row): Row => ({ ...r, input_tokens: Number(r.input_tokens), output_tokens: Number(r.output_tokens) });
  const blocked = (await pool.query(`SELECT count(*)::int AS n FROM agent_usage WHERE status='budget_blocked' AND at >= $1::timestamptz - interval '30 days'`, [now.toISOString()])).rows[0].n;
  return { today: num(today.rows[0]), last30: num(last30.rows[0]), byModel: byModel.rows.map(num), budgetBlocked30d: blocked as number };
}

export async function listAlerts(pool: Pool, limit = 100): Promise<Row[]> {
  return (await pool.query(
    `SELECT a.id, a.kind, a.channel, a.status, a.attempts, a.last_error, a.suppression_reason, a.created_at, a.sent_at, a.opportunity_id,
            o.title_original, o.data_origin FROM alerts a JOIN opportunities o ON o.id = a.opportunity_id ORDER BY a.created_at DESC LIMIT $1`, [limit])).rows;
}

export async function listApplications(pool: Pool): Promise<Row[]> {
  return (await pool.query(
    `SELECT p.id, p.status, p.notes, p.approved_by, p.approved_at, p.submitted_at, p.created_at, p.opportunity_id, o.title_original, o.data_origin, o.canonical_url
       FROM applications p JOIN opportunities o ON o.id = p.opportunity_id ORDER BY p.created_at DESC`)).rows;
}
