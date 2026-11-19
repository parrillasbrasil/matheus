import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { createScratchDb, seedOpportunity, seedSource, type Scratch } from "./helpers/db";

// As rotas leem DATABASE_URL/ADMIN_* do ambiente; definimos antes de importá-las.
let db: Scratch;
let signSession: typeof import("@/lib/auth").signSession;
let hashPassword: typeof import("@/lib/auth").hashPassword;
const SECRET = "t".repeat(48);
const HOST = "painel.test";
let cookie = "";

const req = (path: string, init: RequestInit & { auth?: boolean } = {}) => {
  const { auth = true, headers, ...rest } = init;
  const h = new Headers(headers);
  h.set("host", HOST);
  if (auth && cookie) h.set("cookie", `radar_session=${cookie}`);
  return new Request(`http://${HOST}${path}`, { ...rest, headers: h });
};
const json = (body: unknown) => ({ method: "POST", headers: { "content-type": "application/json", accept: "application/json", origin: `http://${HOST}` }, body: JSON.stringify(body) });

beforeAll(async () => {
  db = await createScratchDb();
  process.env.DATABASE_URL = db.url;
  process.env.ADMIN_USER = "giuliano";
  process.env.SESSION_SECRET = SECRET;
  process.env.COOKIE_SECURE = "false";
  ({ signSession, hashPassword } = await import("@/lib/auth"));
  process.env.ADMIN_PASSWORD_HASH = await hashPassword("senha-de-teste-123");
  cookie = await signSession("giuliano", SECRET, Date.now());
});
afterAll(async () => { const { getPool } = await import("@/lib/db"); await getPool().end(); await db.drop(); });
beforeEach(async () => {
  await db.pool.query("TRUNCATE applications, alerts, scores, eligibility_checks, evidence_spans, opportunity_versions, opportunities, source_runs, documents, sources, jobs, audit_events, service_heartbeats CASCADE");
  await seedSource(db.pool, { id: "src-a" });
});

describe("GET /api/health (público)", () => {
  it("200 'ok' com banco acessível e worker com sinal de vida recente; sem vazar dados", async () => {
    await db.pool.query("INSERT INTO service_heartbeats (service, last_seen) VALUES ('worker', now())");
    const { GET } = await import("@/app/api/health/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ status: "ok", db: true, worker: "ok" });
  });
  it("sem sinal do worker: 200 'degraded' (o painel está de pé, a coleta não)", async () => {
    const { GET } = await import("@/app/api/health/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "degraded", db: true, worker: "never" });
  });
  it("banco fora do ar: 503 'down'", async () => {
    const { GET } = await import("@/app/api/health/route");
    const g = globalThis as unknown as { __radarPool?: import("pg").Pool };
    const real = g.__radarPool;
    const pg = await import("pg");
    g.__radarPool = new pg.default.Pool({ connectionString: "postgresql://postgres@127.0.0.1:1/x", connectionTimeoutMillis: 500 });
    try {
      const res = await GET();
      expect(res.status).toBe(503);
      expect(await res.json()).toEqual({ status: "down", db: false, worker: "unknown" });
    } finally { await g.__radarPool.end().catch(() => undefined); g.__radarPool = real; }
  });
});

describe("autenticação das rotas", () => {
  it("sem cookie: 401 JSON em todas as rotas protegidas", async () => {
    const list = await import("@/app/api/opportunities/route");
    const one = await import("@/app/api/opportunities/[id]/route");
    const sources = await import("@/app/api/sources/route");
    const scan = await import("@/app/api/scan/route");
    const requeue = await import("@/app/api/jobs/[id]/requeue/route");
    const apps = await import("@/app/api/applications/route");
    const appId = await import("@/app/api/applications/[id]/route");
    const calls = [
      list.GET(req("/api/opportunities", { auth: false })),
      one.GET(req("/api/opportunities/x", { auth: false }), { params: Promise.resolve({ id: "x" }) }),
      sources.GET(req("/api/sources", { auth: false })),
      scan.POST(req("/api/scan", { auth: false, ...json({ source_id: "src-a" }) })),
      requeue.POST(req("/api/jobs/1/requeue", { auth: false, ...json({}) }), { params: Promise.resolve({ id: "1" }) }),
      apps.POST(req("/api/applications", { auth: false, ...json({ opportunity_id: "x" }) })),
      appId.POST(req("/api/applications/x", { auth: false, ...json({ status: "withdrawn" }) }), { params: Promise.resolve({ id: "x" }) }),
    ];
    for (const res of await Promise.all(calls)) expect(res.status).toBe(401);
    expect((await db.pool.query("SELECT count(*)::int n FROM jobs")).rows[0].n).toBe(0);
  });
  it("cookie adulterado ou expirado é recusado", async () => {
    const { GET } = await import("@/app/api/sources/route");
    const expired = await signSession("giuliano", SECRET, Date.now() - 24 * 3600_000, 60);
    const forged = await signSession("giuliano", "x".repeat(48), Date.now());
    for (const c of [expired, forged, "lixo"]) {
      const r = new Request(`http://${HOST}/api/sources`, { headers: { host: HOST, cookie: `radar_session=${c}` } });
      expect((await GET(r)).status).toBe(401);
    }
  });
});

describe("POST /api/login e /api/logout", () => {
  const login = (body: object, ip = "9.9.9.9") => ({ method: "POST", auth: false, headers: { "content-type": "application/json", accept: "application/json", origin: `http://${HOST}`, "x-forwarded-for": ip }, body: JSON.stringify(body) });
  it("credenciais corretas: 200 e cookie HttpOnly SameSite=Strict (sem Secure quando COOKIE_SECURE=false)", async () => {
    const { POST } = await import("@/app/api/login/route");
    const res = await POST(req("/api/login", login({ username: "giuliano", password: "senha-de-teste-123" }, "1.1.1.1")));
    expect(res.status).toBe(200);
    const sc = res.headers.get("set-cookie") ?? "";
    expect(sc).toMatch(/^radar_session=/);
    expect(sc).toMatch(/HttpOnly/i);
    expect(sc).toMatch(/SameSite=Strict/i);
    expect(sc).not.toMatch(/Secure/i);
  });
  it("senha errada e usuário errado: 401 com a mesma mensagem (não revela qual falhou)", async () => {
    const { POST } = await import("@/app/api/login/route");
    const a = await POST(req("/api/login", login({ username: "giuliano", password: "errada-errada-1" }, "2.2.2.2")));
    const b = await POST(req("/api/login", login({ username: "outro", password: "senha-de-teste-123" }, "2.2.2.3")));
    expect([a.status, b.status]).toEqual([401, 401]);
    expect(await a.json()).toEqual(await b.json());
    expect(a.headers.get("set-cookie")).toBeNull();
  });
  it("bloqueia o IP após 5 falhas (429), mesmo com a senha certa depois", async () => {
    const { POST } = await import("@/app/api/login/route");
    for (let i = 0; i < 5; i++) await POST(req("/api/login", login({ username: "giuliano", password: "errada-errada-1" }, "3.3.3.3")));
    const res = await POST(req("/api/login", login({ username: "giuliano", password: "senha-de-teste-123" }, "3.3.3.3")));
    expect(res.status).toBe(429);
  });
  it("formulário HTML: sucesso redireciona 303 para o destino seguro; destino externo é ignorado", async () => {
    const { POST } = await import("@/app/api/login/route");
    const form = (next: string, ip: string) => ({ method: "POST", auth: false, headers: { "content-type": "application/x-www-form-urlencoded", origin: `http://${HOST}`, "x-forwarded-for": ip },
      body: new URLSearchParams({ username: "giuliano", password: "senha-de-teste-123", next }).toString() });
    const ok = await POST(req("/api/login", form("/sources", "4.4.4.4")));
    expect(ok.status).toBe(303);
    expect(new URL(ok.headers.get("location")!, `http://${HOST}`).pathname).toBe("/sources");
    const evil = await POST(req("/api/login", form("//evil.com", "4.4.4.5")));
    expect(new URL(evil.headers.get("location")!, `http://${HOST}`).host).toBe(HOST);
  });
  it("logout apaga o cookie", async () => {
    const { POST } = await import("@/app/api/logout/route");
    const res = await POST(req("/api/logout", json({})));
    expect(res.headers.get("set-cookie")).toMatch(/radar_session=;.*Max-Age=0/i);
  });
});

describe("rotas de leitura", () => {
  it("GET /api/opportunities aplica filtros e devolve origem e provas", async () => {
    await seedOpportunity(db.pool, { title: "A", eligibility_status: "eligible", evidence: [{ field: "value", quote: "q", verified: true }] });
    await seedOpportunity(db.pool, { title: "B", eligibility_status: "ineligible" });
    const { GET } = await import("@/app/api/opportunities/route");
    const res = await GET(req("/api/opportunities?eligibility=eligible"));
    const body = await res.json();
    expect(body.items).toHaveLength(1);
    expect(body.items[0]).toMatchObject({ title_original: "A", data_origin: "real", evidence_verified: 1, evidence_total: 1 });
    expect(body.has_next).toBe(false);
  });
  it("GET /api/opportunities/[id]: 200 com detalhes, 404 se não existe, 404 para id malformado", async () => {
    const id = await seedOpportunity(db.pool, { evidence: [{ field: "value", quote: "5.000 euro", verified: true }] });
    const { GET } = await import("@/app/api/opportunities/[id]/route");
    const ok = await GET(req(`/api/opportunities/${id}`), { params: Promise.resolve({ id }) });
    expect(ok.status).toBe(200);
    expect((await ok.json()).evidence[0].quote).toBe("5.000 euro");
    expect((await GET(req("/api/opportunities/00000000-0000-0000-0000-000000000000"), { params: Promise.resolve({ id: "00000000-0000-0000-0000-000000000000" }) })).status).toBe(404);
    expect((await GET(req("/api/opportunities/x"), { params: Promise.resolve({ id: "x" }) })).status).toBe(404);
  });
  it("GET /api/sources lista fontes com ToS e última execução", async () => {
    const { GET } = await import("@/app/api/sources/route");
    const body = await (await GET(req("/api/sources"))).json();
    expect(body.items[0]).toMatchObject({ id: "src-a", tos_status: "allowed" });
  });
});

describe("rotas de escrita", () => {
  it("POST /api/scan enfileira job manual; repetir devolve 409; ToS pendente devolve 409", async () => {
    const { POST } = await import("@/app/api/scan/route");
    const a = await POST(req("/api/scan", json({ source_id: "src-a" })));
    expect(a.status).toBe(202);
    expect((await db.pool.query("SELECT payload FROM jobs")).rows[0].payload).toEqual({ source_id: "src-a", trigger: "manual" });
    expect((await POST(req("/api/scan", json({ source_id: "src-a" })))).status).toBe(409);
    await seedSource(db.pool, { id: "src-tos", tos_status: "pending_review" });
    const r = await POST(req("/api/scan", json({ source_id: "src-tos" })));
    expect(r.status).toBe(409);
    expect((await r.json()).error).toBe("tos_not_allowed");
    expect((await POST(req("/api/scan", json({})))).status).toBe(400);
  });
  it("requisição de outro site (Origin diferente) é recusada com 403 e nada é gravado", async () => {
    const { POST } = await import("@/app/api/scan/route");
    const res = await POST(req("/api/scan", { method: "POST", headers: { "content-type": "application/json", accept: "application/json", origin: "https://evil.example" }, body: JSON.stringify({ source_id: "src-a" }) }));
    expect(res.status).toBe(403);
    expect((await db.pool.query("SELECT count(*)::int n FROM jobs")).rows[0].n).toBe(0);
  });
  it("POST /api/jobs/[id]/requeue devolve job morto à fila", async () => {
    const id = (await db.pool.query(`INSERT INTO jobs (kind, status, attempts, last_error) VALUES ('scan_source','dead',5,'boom') RETURNING id`)).rows[0].id;
    const { POST } = await import("@/app/api/jobs/[id]/requeue/route");
    expect((await POST(req(`/api/jobs/${id}/requeue`, json({})), { params: Promise.resolve({ id: String(id) }) })).status).toBe(200);
    expect((await db.pool.query("SELECT status FROM jobs WHERE id=$1", [id])).rows[0].status).toBe("queued");
    expect((await POST(req(`/api/jobs/${id}/requeue`, json({})), { params: Promise.resolve({ id: String(id) }) })).status).toBe(409);
    expect((await POST(req("/api/jobs/abc/requeue", json({})), { params: Promise.resolve({ id: "abc" }) })).status).toBe(400);
  });
  it("candidaturas: criar rascunho e avançar; a aprovação registra QUEM aprovou (a sessão), não o corpo da requisição", async () => {
    const opp = await seedOpportunity(db.pool);
    const create = (await import("@/app/api/applications/route")).POST;
    const move = (await import("@/app/api/applications/[id]/route")).POST;
    const c = await create(req("/api/applications", json({ opportunity_id: opp })));
    expect(c.status).toBe(201);
    const { id } = await c.json();
    expect((await create(req("/api/applications", json({ opportunity_id: opp })))).status).toBe(409);
    const step = (status: string, extra: object = {}) => move(req(`/api/applications/${id}`, json({ status, ...extra })), { params: Promise.resolve({ id }) });
    expect((await step("approved_by_human")).status).toBe(409);
    expect((await step("awaiting_human_approval")).status).toBe(200);
    expect((await step("approved_by_human", { approved_by: "invasor" })).status).toBe(200);
    expect((await db.pool.query("SELECT approved_by FROM applications WHERE id=$1", [id])).rows[0].approved_by).toBe("giuliano");
    expect((await step("hacked")).status).toBe(400);
  });
});
