import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { createScratchDb, seedOpportunity, seedSource, type Scratch } from "./helpers/db";
import { parseFilters } from "@/lib/filters";
import { listOpportunities, getOpportunity, overview, listSources, listJobs, usageSummary, listAlerts, workerHealth } from "@/lib/queries";

let db: Scratch;
const NOW = new Date("2026-11-19T12:00:00Z");
const days = (n: number) => new Date(NOW.getTime() + n * 86_400_000).toISOString();

beforeAll(async () => { db = await createScratchDb(); });
afterAll(async () => { await db.drop(); });
beforeEach(async () => {
  await db.pool.query("TRUNCATE alerts, applications, scores, eligibility_checks, evidence_spans, opportunity_versions, opportunities, source_runs, documents, sources, jobs, agent_usage, service_heartbeats, audit_events CASCADE");
  await seedSource(db.pool, { id: "src-a" });
  await seedSource(db.pool, { id: "src-fx", origin: "fixture", enabled: false });
});

describe("listOpportunities", () => {
  it("filtra por origem e devolve a origem em cada linha", async () => {
    await seedOpportunity(db.pool, { title: "real-1" });
    await seedOpportunity(db.pool, { source_id: "src-fx", data_origin: "fixture", title: "teste-1" });
    const all = await listOpportunities(db.pool, parseFilters({}));
    expect(all.rows.map((r) => r.data_origin).sort()).toEqual(["fixture", "real"]);
    const real = await listOpportunities(db.pool, parseFilters({ origin: "real" }));
    expect(real.rows.map((r) => r.title_original)).toEqual(["real-1"]);
    const fx = await listOpportunities(db.pool, parseFilters({ origin: "fixture" }));
    expect(fx.rows.map((r) => r.title_original)).toEqual(["teste-1"]);
  });
  it("ordena por score (maior primeiro, nulos por último) e filtra por elegibilidade/status/score mínimo", async () => {
    await seedOpportunity(db.pool, { title: "baixo", score: 10, eligibility_status: "ineligible", status: "closed" });
    await seedOpportunity(db.pool, { title: "alto", score: 90, eligibility_status: "eligible" });
    await seedOpportunity(db.pool, { title: "sem-score", score: null });
    expect((await listOpportunities(db.pool, parseFilters({}))).rows.map((r) => r.title_original)).toEqual(["alto", "baixo", "sem-score"]);
    expect((await listOpportunities(db.pool, parseFilters({ eligibility: "eligible" }))).rows.map((r) => r.title_original)).toEqual(["alto"]);
    expect((await listOpportunities(db.pool, parseFilters({ status: "closed" }))).rows.map((r) => r.title_original)).toEqual(["baixo"]);
    expect((await listOpportunities(db.pool, parseFilters({ min_score: "50" }))).rows.map((r) => r.title_original)).toEqual(["alto"]);
  });
  it("busca por texto sem tratar curingas do usuário como padrão", async () => {
    await seedOpportunity(db.pool, { title: "Bônus 100% Sardenha" });
    await seedOpportunity(db.pool, { title: "Outro programa" });
    expect((await listOpportunities(db.pool, parseFilters({ q: "100%" }))).rows).toHaveLength(1);
    expect((await listOpportunities(db.pool, parseFilters({ q: "%" }))).rows).toHaveLength(1);   // só o título que contém '%'
    expect((await listOpportunities(db.pool, parseFilters({ q: "x'; DROP TABLE opportunities; --" }))).rows).toHaveLength(0);
    expect((await db.pool.query("SELECT count(*)::int n FROM opportunities")).rows[0].n).toBe(2);
  });
  it("pagina e informa se há próxima página", async () => {
    for (let i = 0; i < 27; i++) await seedOpportunity(db.pool, { title: `o${i}`, score: i });
    const p1 = await listOpportunities(db.pool, parseFilters({}));
    expect(p1.rows).toHaveLength(25);
    expect(p1.hasNext).toBe(true);
    const p2 = await listOpportunities(db.pool, parseFilters({ page: "2" }));
    expect(p2.rows).toHaveLength(2);
    expect(p2.hasNext).toBe(false);
  });
  it("conta evidências verificadas e totais só da versão atual", async () => {
    await seedOpportunity(db.pool, { evidence: [{ field: "value", quote: "EUR 5.000", verified: true }, { field: "deadline", quote: "entro il 30", verified: false }] });
    const [r] = (await listOpportunities(db.pool, parseFilters({}))).rows;
    expect([r.evidence_verified, r.evidence_total]).toEqual([1, 2]);
  });
});

describe("getOpportunity", () => {
  it("devolve evidências, checagens, score e histórico de versões", async () => {
    const id = await seedOpportunity(db.pool, {
      evidence: [{ field: "value", quote: "5.000 euro", verified: true }],
      checks: [{ requirement_id: "r1", result: "unknown", reason_pt: "confirmar residência" }],
    });
    await db.pool.query(`INSERT INTO opportunity_versions (opportunity_id, version_no, content_hash, snapshot, change_summary) VALUES ($1,2,'h2','{}','{"fields":{"deadline":{"from":null,"to":"2026-12-01"}}}')`, [id]);
    const o = await getOpportunity(db.pool, id);
    expect(o?.opportunity.id).toBe(id);
    expect(o?.evidence.map((e) => e.quote)).toEqual(["5.000 euro"]);
    expect(o?.checks[0]).toMatchObject({ requirement_id: "r1", result: "unknown" });
    expect(o?.score).toMatchObject({ total: "50" });
    expect(o?.versions.map((v) => v.version_no)).toEqual([2, 1]);
  });
  it("id inexistente ou inválido devolve null (sem erro de banco)", async () => {
    expect(await getOpportunity(db.pool, "00000000-0000-0000-0000-000000000000")).toBeNull();
    expect(await getOpportunity(db.pool, "não-é-uuid'; --")).toBeNull();
  });
});

describe("overview", () => {
  it("separa as três faixas por origem: real, teste e desconhecido nunca se misturam", async () => {
    await seedOpportunity(db.pool, { eligibility_status: "eligible" });
    await seedOpportunity(db.pool, { eligibility_status: "eligible" });
    await seedOpportunity(db.pool, { eligibility_status: "ineligible" });
    await seedOpportunity(db.pool, { source_id: "src-fx", data_origin: "fixture", eligibility_status: "eligible" });
    const o = await overview(db.pool, NOW);
    expect(o.lanes.real).toEqual({ eligible: 2, needs_confirmation: 0, ineligible: 1 });
    expect(o.lanes.fixture).toEqual({ eligible: 1, needs_confirmation: 0, ineligible: 0 });
    expect(o.lanes.unknown).toEqual({ eligible: 0, needs_confirmation: 0, ineligible: 0 });
  });
  it("'encerrando em breve' só inclui abertas, com prazo confirmado e ainda no futuro (até 14 dias)", async () => {
    await seedOpportunity(db.pool, { title: "ok", deadline_at: days(5), deadline_confirmed: true });
    await seedOpportunity(db.pool, { title: "vencida", deadline_at: days(-1), deadline_confirmed: true });
    await seedOpportunity(db.pool, { title: "longe", deadline_at: days(40), deadline_confirmed: true });
    await seedOpportunity(db.pool, { title: "prazo-nao-confirmado", deadline_at: days(3), deadline_confirmed: false });
    await seedOpportunity(db.pool, { title: "fechada", deadline_at: days(3), deadline_confirmed: true, status: "closed" });
    await seedOpportunity(db.pool, { title: "inelegivel", deadline_at: days(3), deadline_confirmed: true, eligibility_status: "ineligible" });
    expect((await overview(db.pool, NOW)).closingSoon.map((r) => r.title_original)).toEqual(["ok"]);
  });
});

describe("fontes, jobs, custos e alertas", () => {
  it("listSources mostra ToS, falhas e a última execução", async () => {
    await seedSource(db.pool, { id: "src-pend", tos_status: "pending_review", consecutive_failures: 3 });
    await db.pool.query(`INSERT INTO source_runs (source_id, status, started_at, items_seen, error) VALUES ('src-a','success','2026-11-19T10:00:00Z',4,NULL),('src-a','failed','2026-11-19T11:00:00Z',0,'timeout')`);
    const rows = await listSources(db.pool);
    const a = rows.find((r) => r.id === "src-a")!;
    expect(a.last_run_status).toBe("failed");
    expect(a.last_run_error).toBe("timeout");
    const p = rows.find((r) => r.id === "src-pend")!;
    expect([p.tos_status, p.consecutive_failures, p.last_run_status]).toEqual(["pending_review", 3, null]);
  });
  it("listJobs devolve contagem por status e a DLQ com o erro", async () => {
    await db.pool.query(`INSERT INTO jobs (kind, status, last_error, payload) VALUES ('scan_source','queued',NULL,'{}'),('scan_source','dead','HTTP 500','{"source_id":"src-a"}'),('scan_source','done',NULL,'{}')`);
    const j = await listJobs(db.pool);
    expect(j.stats).toEqual({ queued: 1, running: 0, done: 1, dead: 1 });
    expect(j.dead).toHaveLength(1);
    expect(j.dead[0]).toMatchObject({ last_error: "HTTP 500" });
  });
  it("usageSummary soma tokens e sinaliza custo desconhecido em vez de tratá-lo como zero", async () => {
    await db.pool.query(
      `INSERT INTO agent_usage (at, provider, model, purpose, input_tokens, output_tokens, cost_usd, cost_known, status) VALUES
       ($1,'openai','m1','extraction',1000,200,0.01,true,'ok'),
       ($1,'openai','m1','extraction',500,100,NULL,false,'ok'),
       ($1,'anthropic','m2','audit',300,50,NULL,false,'ok'),
       ('2026-09-01T00:00:00Z','openai','m1','extraction',9999,9999,1,true,'ok')`, [NOW.toISOString()]);
    const u = await usageSummary(db.pool, NOW);
    expect(u.today).toMatchObject({ input_tokens: 1800, output_tokens: 350, calls: 3, unknown_cost_calls: 2 });
    expect(Number(u.today.known_cost_usd)).toBeCloseTo(0.01);
    expect(u.byModel.find((m) => m.provider === "openai")).toMatchObject({ calls: 2, input_tokens: 1500 });
  });
  it("listAlerts devolve status e motivo de supressão", async () => {
    const id = await seedOpportunity(db.pool);
    await db.pool.query(`INSERT INTO alerts (opportunity_id, kind, channel, dedupe_key, status, suppression_reason) VALUES ($1,'new','telegram','k1','suppressed','encerrada')`, [id]);
    expect((await listAlerts(db.pool))[0]).toMatchObject({ status: "suppressed", suppression_reason: "encerrada" });
  });
  it("workerHealth: sem heartbeat = 'never', recente = 'ok', antigo = 'stale'", async () => {
    expect((await workerHealth(db.pool, NOW)).state).toBe("never");
    await db.pool.query(`INSERT INTO service_heartbeats (service, last_seen) VALUES ('worker',$1)`, [new Date(NOW.getTime() - 30_000).toISOString()]);
    expect((await workerHealth(db.pool, NOW)).state).toBe("ok");
    await db.pool.query(`UPDATE service_heartbeats SET last_seen=$1`, [new Date(NOW.getTime() - 3600_000).toISOString()]);
    expect((await workerHealth(db.pool, NOW)).state).toBe("stale");
  });
});
