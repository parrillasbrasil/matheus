// Banco descartável para testes: cria um database novo, aplica as migrations REAIS do projeto e semeia dados rotulados como teste.
import pg from "pg";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

export const ADMIN_URL = process.env.RADAR_TEST_DATABASE_URL ?? "postgresql://postgres@127.0.0.1:54329/postgres";
const MIGRATIONS = process.env.RADAR_MIGRATIONS_DIR ?? path.resolve(__dirname, "../../../db/migrations");

export type Scratch = { url: string; pool: pg.Pool; drop: () => Promise<void> };

export async function createScratchDb(): Promise<Scratch> {
  const name = `web_test_${crypto.randomBytes(5).toString("hex")}`;
  const admin = new pg.Client({ connectionString: ADMIN_URL });
  await admin.connect();
  await admin.query(`CREATE DATABASE ${name}`);
  await admin.end();
  const url = ADMIN_URL.replace(/\/[^/]*$/, `/${name}`);
  const pool = new pg.Pool({ connectionString: url, max: 4 });
  for (const f of fs.readdirSync(MIGRATIONS).filter((x) => x.endsWith(".sql")).sort()) {
    await pool.query(fs.readFileSync(path.join(MIGRATIONS, f), "utf8"));
  }
  return {
    url, pool,
    drop: async () => {
      await pool.end();
      const a = new pg.Client({ connectionString: ADMIN_URL });
      await a.connect();
      await a.query(`DROP DATABASE IF EXISTS ${name} WITH (FORCE)`);
      await a.end();
    },
  };
}

export async function seedSource(pool: pg.Pool, o: Partial<{ id: string; origin: string; tos_status: string; enabled: boolean; name: string; kind: string; consecutive_failures: number }> = {}) {
  const s = { id: "src-a", origin: "real", tos_status: "allowed", enabled: true, name: "Fonte A", kind: "web", consecutive_failures: 0, ...o };
  await pool.query(
    `INSERT INTO sources (id, name, kind, origin, enabled, url, interval_minutes, rate_limit, tos_status, consecutive_failures)
     VALUES ($1,$2,$3,$4,$5,'https://example.test/' || $1, 60, '{}', $6, $7) ON CONFLICT (id) DO NOTHING`,
    [s.id, s.name, s.kind, s.origin, s.enabled, s.tos_status, s.consecutive_failures]);
  return s.id;
}

export type OppSeed = Partial<{
  source_id: string; data_origin: string; title: string; country: string; category: string; status: string; eligibility_status: string;
  score: number | null; deadline_at: string | null; deadline_confirmed: boolean; value_amount: number | null; updated_at: string;
  evidence: { field: string; quote: string; verified: boolean }[]; checks: { requirement_id: string; result: string; reason_pt: string }[];
}>;

export async function seedOpportunity(pool: pg.Pool, o: OppSeed = {}): Promise<string> {
  const d = { source_id: "src-a", data_origin: "real", title: "Bônus de mudança (TESTE)", country: "IT", category: "gov_relocation_incentive", status: "open",
    eligibility_status: "needs_confirmation", score: 50 as number | null, deadline_at: null as string | null, deadline_confirmed: false, value_amount: null as number | null,
    updated_at: new Date().toISOString(), evidence: [] as NonNullable<OppSeed["evidence"]>, checks: [] as NonNullable<OppSeed["checks"]>, ...o };
  const key = `${d.source_id}|${crypto.randomUUID()}`;
  const opp = (await pool.query(
    `INSERT INTO opportunities (opportunity_key, source_id, canonical_url, is_official, data_origin, title_original, title_pt, country, category, status,
       eligibility_status, relocation_condition, extraction, extraction_origin, last_verified_at, content_hash, current_score, deadline_at, deadline_confirmed,
       value_amount, value_currency, updated_at)
     VALUES ($1,$2,'https://example.test/x/' || $1,true,$3,$4,$4,$5,$6,$7,$8,'none','{}','llm',now(),'h',$9,$10,$11,$12,'EUR',$13) RETURNING id`,
    [key, d.source_id, d.data_origin, d.title, d.country, d.category, d.status, d.eligibility_status, d.score, d.deadline_at, d.deadline_confirmed, d.value_amount, d.updated_at])).rows[0].id;
  const ver = (await pool.query(
    `INSERT INTO opportunity_versions (opportunity_id, version_no, content_hash, snapshot, change_summary) VALUES ($1,1,'h','{}','{"created":true}') RETURNING id`, [opp])).rows[0].id;
  await pool.query("UPDATE opportunities SET current_version_id=$1 WHERE id=$2", [ver, opp]);
  for (const e of d.evidence) {
    await pool.query(`INSERT INTO evidence_spans (opportunity_id, version_id, field, quote, verified, source_url) VALUES ($1,$2,$3,$4,$5,'https://example.test/x')`, [opp, ver, e.field, e.quote, e.verified]);
  }
  for (const c of d.checks) {
    await pool.query(`INSERT INTO eligibility_checks (opportunity_id, version_id, requirement_id, kind, result, reason_pt, origin) VALUES ($1,$2,$3,'other',$4,$5,'extracted')`, [opp, ver, c.requirement_id, c.result, c.reason_pt]);
  }
  if (d.score !== null) {
    await pool.query(`INSERT INTO scores (opportunity_id, version_id, total, components, explanation) VALUES ($1,$2,$3,'{"eligibility":20}','["elegibilidade: 20"]')`, [opp, ver, d.score]);
  }
  return opp;
}
