import fs from "node:fs";
import path from "node:path";
import pg from "pg";
import YAML from "yaml";

// Pool único por processo (também sobrevive ao hot reload em desenvolvimento).
const g = globalThis as unknown as { __radarPool?: pg.Pool };

type MockSource = {
  id: string;
  name: string;
  kind: string;
  role: string;
  origin: string;
  enabled: boolean;
  url: string;
  country: string | null;
  tos_status: string;
  tos_note: string | null;
  tos_reviewed_on: string | null;
  tos_evidence_url: string | null;
  interval_minutes: number;
  last_run_at: string | null;
  next_run_at: string | null;
  consecutive_failures: number;
  last_run_status: string | null;
  last_run_error: string | null;
  last_run_items: number | null;
  last_run_started_at: string | null;
  opportunities: number;
};

type MockJob = {
  id: number;
  kind: string;
  payload: Record<string, unknown>;
  status: "queued" | "running" | "done" | "dead";
  attempts: number;
  max_attempts: number;
  run_at: string;
  dedupe_key: string | null;
  last_error: string | null;
  finished_at: string | null;
  created_at: string;
};

type ConfigSource = {
  id?: string;
  name?: string;
  kind?: string;
  role?: string;
  origin?: string;
  enabled?: boolean;
  url?: string;
  country?: string | null;
  interval_minutes?: number;
  tos?: { status?: string; note?: string; reviewed_on?: string; evidence_url?: string };
};

function loadConfigSources(): MockSource[] {
  const candidates = [
    path.resolve(process.cwd(), "..", "config.yaml"),
    path.resolve(process.cwd(), "config.yaml"),
  ];
  for (const file of candidates) {
    try {
      if (!fs.existsSync(file)) continue;
      const parsed = YAML.parse(fs.readFileSync(file, "utf8")) as { sources?: ConfigSource[] } | null;
      if (!Array.isArray(parsed?.sources)) continue;
      return parsed.sources
        .filter((s): s is ConfigSource & { id: string } => typeof s?.id === "string")
        .map((s) => ({
          id: s.id,
          name: s.name ?? s.id,
          kind: s.kind ?? "html_page",
          role: s.role ?? "opportunities",
          origin: s.origin ?? "real",
          enabled: Boolean(s.enabled),
          url: s.url ?? "",
          country: s.country ?? null,
          tos_status: s.tos?.status ?? "pending_review",
          tos_note: s.tos?.note ?? null,
          tos_reviewed_on: s.tos?.reviewed_on ? String(s.tos.reviewed_on) : null,
          tos_evidence_url: s.tos?.evidence_url ?? null,
          interval_minutes: s.interval_minutes ?? 720,
          last_run_at: null,
          next_run_at: null,
          consecutive_failures: 0,
          last_run_status: null,
          last_run_error: null,
          last_run_items: null,
          last_run_started_at: null,
          opportunities: 0,
        }));
    } catch {
      // ignora erro de leitura no modo em memória
    }
  }
  return [];
}

function createMockPool(): pg.Pool {
  console.warn("[AI Studio] DATABASE_URL não definida — usando mock PostgreSQL em memória com fontes do config.yaml.");
  const sources = loadConfigSources();
  const jobs: MockJob[] = [];
  let nextJobId = 1;

  const query = async (text: string, values: unknown[] = []) => {
    const sql = text.replace(/\s+/g, " ").trim();

    if (/^(BEGIN|COMMIT|ROLLBACK)$/i.test(sql) || sql.includes("pg_advisory_xact_lock")) {
      return { rows: [], rowCount: 0 };
    }
    if (/^SELECT 1$/i.test(sql)) {
      return { rows: [{ "?column?": 1 }], rowCount: 1 };
    }
    if (sql.startsWith("INSERT INTO audit_events")) {
      return { rows: [], rowCount: 1 };
    }
    if (sql.startsWith("SELECT enabled, origin, tos_status FROM sources WHERE id=")) {
      const src = sources.find((s) => s.id === values[0]);
      return {
        rows: src ? [{ enabled: src.enabled, origin: src.origin, tos_status: src.tos_status }] : [],
        rowCount: src ? 1 : 0,
      };
    }
    if (sql.startsWith("INSERT INTO jobs")) {
      const payloadRaw = typeof values[0] === "string" ? values[0] : "{}";
      const runAt = typeof values[1] === "string" ? values[1] : new Date().toISOString();
      const dedupeKey = typeof values[2] === "string" ? values[2] : null;
      if (dedupeKey && jobs.some((j) => j.dedupe_key === dedupeKey && (j.status === "queued" || j.status === "running"))) {
        return { rows: [], rowCount: 0 };
      }
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(payloadRaw) as Record<string, unknown>; } catch { /* vazio */ }
      const id = nextJobId++;
      jobs.push({
        id,
        kind: "scan_source",
        payload,
        status: "queued",
        attempts: 0,
        max_attempts: 5,
        run_at: runAt,
        dedupe_key: dedupeKey,
        last_error: null,
        finished_at: null,
        created_at: runAt,
      });
      return { rows: [{ id }], rowCount: 1 };
    }
    if (sql.startsWith("UPDATE jobs SET status='queued'")) {
      const id = Number(values[0]);
      const runAt = typeof values[1] === "string" ? values[1] : new Date().toISOString();
      const job = jobs.find((j) => j.id === id && j.status === "dead");
      if (!job) return { rows: [], rowCount: 0 };
      if (job.dedupe_key && jobs.some((j) => j.id !== job.id && j.dedupe_key === job.dedupe_key && (j.status === "queued" || j.status === "running"))) {
        const err = new Error("duplicate key") as Error & { code?: string };
        err.code = "23505";
        throw err;
      }
      job.status = "queued";
      job.attempts = 0;
      job.run_at = runAt;
      job.last_error = null;
      job.finished_at = null;
      return { rows: [], rowCount: 1 };
    }
    if (sql.includes("FROM jobs GROUP BY 1")) {
      const counts = new Map<string, number>();
      for (const j of jobs) counts.set(j.status, (counts.get(j.status) ?? 0) + 1);
      const rows = [...counts.entries()].map(([status, n]) => ({ status, n }));
      return { rows, rowCount: rows.length };
    }
    if (sql.includes("FROM jobs WHERE status='dead'")) {
      const rows = jobs.filter((j) => j.status === "dead").slice(0, 50);
      return { rows, rowCount: rows.length };
    }
    if (sql.includes("FROM jobs WHERE status IN ('queued','running')")) {
      const rows = jobs.filter((j) => j.status === "queued" || j.status === "running").slice(0, 50);
      return { rows, rowCount: rows.length };
    }
    if (sql.includes("AS failing") && sql.includes("FROM sources WHERE origin='real'")) {
      const real = sources.filter((s) => s.origin === "real");
      return {
        rows: [{
          failing: real.filter((s) => s.enabled && s.consecutive_failures > 0).length,
          pending_tos: real.filter((s) => s.tos_status === "pending_review").length,
          active: real.filter((s) => s.enabled && s.tos_status === "allowed").length,
        }],
        rowCount: 1,
      };
    }
    if (sql.includes("FROM sources s")) {
      const sorted = [...sources].sort((a, b) => {
        const ar = a.origin === "real" ? 1 : 0;
        const br = b.origin === "real" ? 1 : 0;
        if (ar !== br) return br - ar;
        if (a.enabled !== b.enabled) return a.enabled ? -1 : 1;
        return a.id.localeCompare(b.id);
      });
      return { rows: sorted, rowCount: sorted.length };
    }
    if (sql.includes("FROM agent_usage WHERE status='budget_blocked'")) {
      return { rows: [{ n: 0 }], rowCount: 1 };
    }
    if (sql.includes("FROM agent_usage") && sql.includes("coalesce(sum(input_tokens),0)")) {
      return {
        rows: [{
          input_tokens: "0",
          output_tokens: "0",
          calls: 0,
          unknown_cost_calls: 0,
          known_cost_usd: "0",
          cached_calls: 0,
        }],
        rowCount: 1,
      };
    }
    return { rows: [], rowCount: 0 };
  };

  return {
    query,
    connect: async () => ({ query, release: () => undefined }),
    on: () => undefined,
    end: async () => undefined,
  } as unknown as pg.Pool;
}

// NUMERIC e BIGINT chegam como string por padrão; mantemos assim para não perder precisão. Contagens usam ::int nas consultas.
export function getPool(): pg.Pool {
  if (!g.__radarPool) {
    const url = process.env.DATABASE_URL;
    if (!url) {
      g.__radarPool = createMockPool();
    } else {
      g.__radarPool = new pg.Pool({ connectionString: url, max: 5, idleTimeoutMillis: 30_000, statement_timeout: 15_000 });
      g.__radarPool.on("error", () => { /* conexão ociosa caiu: o pool reabre na próxima consulta */ });
    }
  }
  return g.__radarPool;
}
