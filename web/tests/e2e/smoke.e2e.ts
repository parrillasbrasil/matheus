// Ponta a ponta: sobe o app COMPILADO (`npm run build` antes) contra um banco descartável e navega como um usuário.
// Os dados semeados são de TESTE (fixture) e a tela precisa mostrá-los como tal.
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { spawn, type ChildProcess } from "node:child_process";
import { createScratchDb, seedOpportunity, seedSource, type Scratch } from "../helpers/db";
import { hashPassword } from "@/lib/auth";

const PORT = 3100 + Math.floor(Math.random() * 400);
const BASE = `http://127.0.0.1:${PORT}`;
const PASSWORD = "senha-e2e-de-teste-1";
let db: Scratch, server: ChildProcess, oppId: string, cookie = "";
const get = (path: string, init: RequestInit = {}) => fetch(`${BASE}${path}`, { redirect: "manual", ...init, headers: { ...(init.headers ?? {}), ...(cookie ? { cookie } : {}) } });

beforeAll(async () => {
  db = await createScratchDb();
  await seedSource(db.pool, { id: "fx-e2e", origin: "fixture", enabled: false, name: "Fonte de teste E2E" });
  await seedSource(db.pool, { id: "real-allowed" });
  oppId = await seedOpportunity(db.pool, {
    source_id: "fx-e2e", data_origin: "fixture", title: "Bônus de mudança (TESTE E2E)", eligibility_status: "needs_confirmation", score: 61,
    evidence: [{ field: "value", quote: "contributo di 5.000 euro", verified: true }],
    checks: [{ requirement_id: "r1", result: "unknown", reason_pt: "Confirmar residência na região" }],
  });
  server = spawn("npx", ["next", "start", "-p", String(PORT), "-H", "127.0.0.1"], {
    env: { ...process.env, DATABASE_URL: db.url, ADMIN_USER: "giuliano", ADMIN_PASSWORD_HASH: await hashPassword(PASSWORD), SESSION_SECRET: "e".repeat(48), COOKIE_SECURE: "false", NEXT_TELEMETRY_DISABLED: "1" },
    stdio: "ignore",
  });
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(`${BASE}/api/health`)).status < 500) return; } catch { /* ainda subindo */ }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("servidor não subiu");
});
afterAll(async () => { server?.kill("SIGTERM"); await db?.drop(); });

describe("app compilado + banco real", () => {
  it("healthcheck é público", async () => {
    const r = await get("/api/health");
    expect(r.status).toBe(200);
    expect((await r.json()).db).toBe(true);
  });
  it("sem sessão: páginas redirecionam ao login e a API responde 401", async () => {
    const page = await get("/opportunities?origin=real");
    expect([302, 307, 308]).toContain(page.status);
    expect(page.headers.get("location")).toContain("/login?next=");
    expect((await get("/api/opportunities")).status).toBe(401);
    expect((await get("/api/scan", { method: "POST" })).status).toBe(401);
  });
  it("login com senha errada falha; certa entrega cookie HttpOnly", async () => {
    const bad = await fetch(`${BASE}/api/login`, { method: "POST", redirect: "manual", headers: { "content-type": "application/x-www-form-urlencoded", "x-forwarded-for": "7.7.7.7" }, body: new URLSearchParams({ username: "giuliano", password: "errada-errada-1" }) });
    expect(bad.status).toBe(303);
    expect(bad.headers.get("location")).toContain("/login?error=invalid_credentials");
    const ok = await fetch(`${BASE}/api/login`, { method: "POST", redirect: "manual", headers: { "content-type": "application/x-www-form-urlencoded", "x-forwarded-for": "7.7.7.8" }, body: new URLSearchParams({ username: "giuliano", password: PASSWORD, next: "/opportunities" }) });
    expect(ok.status).toBe(303);
    expect(ok.headers.get("location")).toBe("/opportunities");
    const sc = ok.headers.get("set-cookie")!;
    expect(sc).toMatch(/HttpOnly/i);
    cookie = sc.split(";")[0];
  });
  it("todas as páginas abrem com sessão", async () => {
    for (const p of ["/", "/opportunities", "/sources", "/jobs", "/costs", "/alerts", "/applications"]) {
      const r = await get(p);
      expect(r.status, p).toBe(200);
    }
  });
  it("visão geral mostra as três origens e conta o dado de teste só na linha de teste", async () => {
    const html = await (await get("/")).text();
    expect(html).toContain("Dado real");
    expect(html).toContain("TESTE · não é pesquisa real");
    expect(html).toContain("Origem desconhecida");
    expect(html).toContain("Nenhuma oportunidade real ainda");
    expect(html).toContain("O coletor ainda não deu sinal de vida");   // worker nunca iniciou neste banco
  });
  it("lista e detalhe rotulam o dado de teste e mostram a evidência conferida", async () => {
    const list = await (await get("/opportunities")).text();
    expect(list).toContain("Bônus de mudança (TESTE E2E)");
    expect(list).toContain("TESTE · não é pesquisa real");
    expect(list).toContain("Precisa confirmar");
    const detail = await (await get(`/opportunities/${oppId}`)).text();
    expect(detail).toContain("contributo di 5.000 euro");
    expect(detail).toContain("Conferida literalmente no texto");
    expect(detail).toContain("Confirmar residência na região");
    expect(detail).toContain("O radar nunca envia candidaturas");
    expect((await get("/opportunities/00000000-0000-0000-0000-000000000000")).status).toBe(404);
  });
  it("filtro por origem real não mostra o dado de teste", async () => {
    const html = await (await get("/opportunities?origin=real")).text();
    expect(html).not.toContain("Bônus de mudança (TESTE E2E)");
    expect(html).toContain("Nenhuma oportunidade com esses filtros");
  });
  it("coleta manual: fonte real permitida enfileira; fonte de teste é recusada; formulário volta com aviso", async () => {
    const ok = await get("/api/scan", { method: "POST", headers: { "content-type": "application/json", accept: "application/json", origin: BASE }, body: JSON.stringify({ source_id: "real-allowed" }) });
    expect(ok.status).toBe(202);
    expect((await db.pool.query("SELECT payload FROM jobs")).rows[0].payload).toEqual({ source_id: "real-allowed", trigger: "manual" });
    const form = await get("/api/scan", { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded", origin: BASE }, body: new URLSearchParams({ source_id: "fx-e2e", next: "/sources" }) });
    expect(form.status).toBe(303);
    expect(form.headers.get("location")).toBe("/sources?error=disabled");
    const page = await (await get("/sources?error=disabled")).text();
    expect(page).toContain("A fonte está desabilitada no config.yaml.");
    expect((await get("/sources?error=%3Cscript%3E")).status).toBe(200);
    expect(await (await get("/sources?error=%3Cscript%3E")).text()).not.toContain("<script>alert");
  });
  it("logout encerra a sessão", async () => {
    const out = await get("/api/logout", { method: "POST", headers: { origin: BASE } });
    expect(out.status).toBe(303);
    expect(out.headers.get("set-cookie")).toMatch(/Max-Age=0/i);
  });
});
