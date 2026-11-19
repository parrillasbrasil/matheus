import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { createScratchDb, seedOpportunity, seedSource, type Scratch } from "./helpers/db";
import { enqueueScan, requeueDeadJob, createApplicationDraft, transitionApplication } from "@/lib/actions";

let db: Scratch;
const NOW = new Date("2026-11-19T12:00:00Z");

beforeAll(async () => { db = await createScratchDb(); });
afterAll(async () => { await db.drop(); });
beforeEach(async () => {
  await db.pool.query("TRUNCATE applications, alerts, scores, eligibility_checks, evidence_spans, opportunity_versions, opportunities, source_runs, documents, sources, jobs, audit_events CASCADE");
  await seedSource(db.pool, { id: "src-a" });
});

describe("enqueueScan (execução manual)", () => {
  it("enfileira scan_source manual com dedupe_key igual ao do agendador e registra auditoria", async () => {
    const r = await enqueueScan(db.pool, "src-a", "giuliano", NOW);
    expect(r).toMatchObject({ ok: true });
    const job = (await db.pool.query("SELECT kind, payload, dedupe_key, status, max_attempts FROM jobs")).rows[0];
    expect(job).toMatchObject({ kind: "scan_source", dedupe_key: "scan:src-a", status: "queued" });
    expect(job.payload).toEqual({ source_id: "src-a", trigger: "manual" });
    const ev = (await db.pool.query("SELECT actor, event, entity_id FROM audit_events")).rows[0];
    expect(ev).toEqual({ actor: "giuliano", event: "scan_requested", entity_id: "src-a" });
  });
  it("não duplica: segundo pedido com o job ainda na fila devolve already_queued", async () => {
    await enqueueScan(db.pool, "src-a", "u", NOW);
    expect(await enqueueScan(db.pool, "src-a", "u", NOW)).toEqual({ ok: false, reason: "already_queued" });
    expect((await db.pool.query("SELECT count(*)::int n FROM jobs")).rows[0].n).toBe(1);
  });
  it("recusa fonte inexistente, desabilitada, de teste ou com ToS não aprovado (sem criar job)", async () => {
    await seedSource(db.pool, { id: "src-off", enabled: false });
    await seedSource(db.pool, { id: "src-tos", tos_status: "pending_review" });
    await seedSource(db.pool, { id: "src-forb", tos_status: "forbidden" });
    expect(await enqueueScan(db.pool, "nao-existe", "u", NOW)).toEqual({ ok: false, reason: "unknown_source" });
    expect(await enqueueScan(db.pool, "src-off", "u", NOW)).toEqual({ ok: false, reason: "disabled" });
    expect(await enqueueScan(db.pool, "src-tos", "u", NOW)).toEqual({ ok: false, reason: "tos_not_allowed" });
    expect(await enqueueScan(db.pool, "src-forb", "u", NOW)).toEqual({ ok: false, reason: "tos_not_allowed" });
    expect((await db.pool.query("SELECT count(*)::int n FROM jobs")).rows[0].n).toBe(0);
  });
});

describe("requeueDeadJob (DLQ)", () => {
  it("devolve job morto à fila com tentativas zeradas e audita", async () => {
    const id = (await db.pool.query(`INSERT INTO jobs (kind, status, attempts, last_error, dedupe_key, payload) VALUES ('scan_source','dead',5,'HTTP 500','scan:src-a','{"source_id":"src-a"}') RETURNING id`)).rows[0].id;
    expect(await requeueDeadJob(db.pool, Number(id), "u", NOW)).toEqual({ ok: true });
    expect((await db.pool.query("SELECT status, attempts, last_error FROM jobs WHERE id=$1", [id])).rows[0]).toEqual({ status: "queued", attempts: 0, last_error: null });
    expect((await db.pool.query("SELECT event FROM audit_events")).rows[0].event).toBe("job_requeued");
  });
  it("não mexe em job que não está morto; informa conflito se já há outro na fila com a mesma chave", async () => {
    const ok = (await db.pool.query(`INSERT INTO jobs (kind, status) VALUES ('scan_source','done') RETURNING id`)).rows[0].id;
    expect(await requeueDeadJob(db.pool, Number(ok), "u", NOW)).toEqual({ ok: false, reason: "not_dead" });
    await db.pool.query(`INSERT INTO jobs (kind, status, dedupe_key) VALUES ('scan_source','queued','scan:src-a')`);
    const dead = (await db.pool.query(`INSERT INTO jobs (kind, status, dedupe_key) VALUES ('scan_source','dead','scan:src-a') RETURNING id`)).rows[0].id;
    expect(await requeueDeadJob(db.pool, Number(dead), "u", NOW)).toEqual({ ok: false, reason: "already_queued" });
    expect((await db.pool.query("SELECT status FROM jobs WHERE id=$1", [dead])).rows[0].status).toBe("dead");
  });
});

describe("candidaturas: o sistema nunca envia, só uma pessoa avança o fluxo", () => {
  it("cria rascunho e não cria um segundo enquanto houver um ativo", async () => {
    const opp = await seedOpportunity(db.pool);
    const r = await createApplicationDraft(db.pool, opp, "u", NOW);
    expect(r).toMatchObject({ ok: true });
    expect((await db.pool.query("SELECT status FROM applications")).rows).toEqual([{ status: "draft" }]);
    expect(await createApplicationDraft(db.pool, opp, "u", NOW)).toEqual({ ok: false, reason: "already_exists" });
    expect(await createApplicationDraft(db.pool, "00000000-0000-0000-0000-000000000000", "u", NOW)).toEqual({ ok: false, reason: "unknown_opportunity" });
  });
  it("só segue a ordem rascunho → aguardando aprovação → aprovada por pessoa → enviada por pessoa", async () => {
    const opp = await seedOpportunity(db.pool);
    const { id } = (await createApplicationDraft(db.pool, opp, "u", NOW)) as { ok: true; id: string };
    expect(await transitionApplication(db.pool, id, "approved_by_human", "u", NOW)).toEqual({ ok: false, reason: "invalid_transition" });
    expect(await transitionApplication(db.pool, id, "submitted_by_human", "u", NOW)).toEqual({ ok: false, reason: "invalid_transition" });
    expect(await transitionApplication(db.pool, id, "awaiting_human_approval", "u", NOW)).toEqual({ ok: true });
    expect(await transitionApplication(db.pool, id, "approved_by_human", "maria", NOW)).toEqual({ ok: true });
    let row = (await db.pool.query("SELECT status, approved_by, approved_at, submitted_at FROM applications WHERE id=$1", [id])).rows[0];
    expect(row).toMatchObject({ status: "approved_by_human", approved_by: "maria" });
    expect(row.submitted_at).toBeNull();
    expect(await transitionApplication(db.pool, id, "submitted_by_human", "maria", NOW)).toEqual({ ok: true });
    row = (await db.pool.query("SELECT status, submitted_at FROM applications WHERE id=$1", [id])).rows[0];
    expect(row.status).toBe("submitted_by_human");
    expect(row.submitted_at).not.toBeNull();
    expect((await db.pool.query("SELECT event FROM audit_events WHERE entity_type='application' ORDER BY id")).rows.map((r) => r.event))
      .toEqual(["application_created", "application_awaiting_human_approval", "application_approved_by_human", "application_submitted_by_human"]);
  });
  it("pode retirar a candidatura antes de enviada, mas não depois", async () => {
    const opp = await seedOpportunity(db.pool);
    const { id } = (await createApplicationDraft(db.pool, opp, "u", NOW)) as { ok: true; id: string };
    expect(await transitionApplication(db.pool, id, "withdrawn", "u", NOW)).toEqual({ ok: true });
    expect(await transitionApplication(db.pool, id, "draft", "u", NOW)).toEqual({ ok: false, reason: "invalid_transition" });
    expect(await createApplicationDraft(db.pool, opp, "u", NOW)).toMatchObject({ ok: true });   // retirada não bloqueia novo rascunho
  });
  it("rejeita status desconhecido e id inexistente", async () => {
    expect(await transitionApplication(db.pool, "00000000-0000-0000-0000-000000000000", "withdrawn", "u", NOW)).toEqual({ ok: false, reason: "unknown_application" });
    expect(await transitionApplication(db.pool, "não-uuid", "withdrawn", "u", NOW)).toEqual({ ok: false, reason: "unknown_application" });
    const opp = await seedOpportunity(db.pool);
    const { id } = (await createApplicationDraft(db.pool, opp, "u", NOW)) as { ok: true; id: string };
    expect(await transitionApplication(db.pool, id, "hacked" as never, "u", NOW)).toEqual({ ok: false, reason: "invalid_transition" });
  });
});

describe("enqueueScan: fonte de teste", () => {
  it("fonte fixture nunca é enfileirada pelo painel", async () => {
    await seedSource(db.pool, { id: "src-fx", origin: "fixture", enabled: true });
    expect(await enqueueScan(db.pool, "src-fx", "u", NOW)).toEqual({ ok: false, reason: "fixture_source" });
  });
});
