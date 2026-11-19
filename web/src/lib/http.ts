// Utilitários comuns das rotas: sessão, checagem de origem (CSRF), leitura do corpo e resposta JSON/redirecionamento.
import { assertAuthConfig, isAuthConfigured, safeNext, SESSION_COOKIE, verifySession, type AuthConfig } from "./auth";

export type Body = Record<string, string>;

export function authConfig(): AuthConfig | Response {
  try { return assertAuthConfig(process.env); }
  catch { return Response.json({ error: "auth_not_configured" }, { status: 500 }); }
}

export function readCookie(req: Request, name: string): string | undefined {
  for (const part of (req.headers.get("cookie") ?? "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return undefined;
}

/** Devolve o usuário da sessão ou uma Response 401/500. Defesa em profundidade: o proxy também bloqueia. */
export async function requireUser(req: Request): Promise<{ user: string } | Response> {
  if (!isAuthConfigured(process.env)) return { user: "admin (preview)" };
  const cfg = authConfig();
  if (cfg instanceof Response) return cfg;
  const s = await verifySession(readCookie(req, SESSION_COOKIE), cfg.secret, Date.now());
  return s ?? Response.json({ error: "unauthorized" }, { status: 401 });
}

/** Pedidos que alteram estado só valem se vierem do próprio site. Sem Origin (curl, scripts) segue: o cookie é SameSite=Strict. */
export function sameOrigin(req: Request): boolean {
  const origin = req.headers.get("origin");
  if (origin) {
    try {
      const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host") ?? new URL(req.url).host;
      return new URL(origin).host === host;
    } catch { return false; }
  }
  if (req.headers.get("sec-fetch-site") === "cross-site") return false;
  return true;
}

export async function readBody(req: Request): Promise<Body> {
  const type = req.headers.get("content-type") ?? "";
  try {
    if (type.includes("application/json")) {
      const raw = await req.json();
      return Object.fromEntries(Object.entries(raw && typeof raw === "object" ? raw : {}).filter(([, v]) => typeof v === "string" || typeof v === "number").map(([k, v]) => [k, String(v)]));
    }
    if (type.includes("form")) {
      const f = await req.formData();
      return Object.fromEntries([...f.entries()].filter(([, v]) => typeof v === "string") as [string, string][]);
    }
  } catch { /* corpo inválido = corpo vazio */ }
  return {};
}

export const wantsJson = (req: Request) => (req.headers.get("accept") ?? "").includes("application/json") || (req.headers.get("content-type") ?? "").includes("application/json");

/** JSON para clientes de API; 303 de volta à página (com ?notice= ou ?error=) para formulários HTML. */
export function outcome(req: Request, body: Body, status: number, payload: object, code: string): Response {
  if (wantsJson(req)) return Response.json(payload, { status });
  const url = new URL(safeNext(body.next ?? refererPath(req)), "http://local");
  url.searchParams.delete("notice"); url.searchParams.delete("error");
  url.searchParams.set(status < 400 ? "notice" : "error", code);
  return new Response(null, { status: 303, headers: { location: `${url.pathname}${url.search}` } });
}

function refererPath(req: Request): string {
  try { const u = new URL(req.headers.get("referer") ?? ""); return `${u.pathname}${u.search}`; } catch { return "/"; }
}

/** Guarda comum das rotas POST: sessão + mesma origem + corpo. */
export async function guardPost(req: Request): Promise<{ user: string; body: Body } | Response> {
  const u = await requireUser(req);
  if (u instanceof Response) return u;
  if (!sameOrigin(req)) return Response.json({ error: "cross_origin_blocked" }, { status: 403 });
  return { user: u.user, body: await readBody(req) };
}

export const STATUS_BY_REASON: Record<string, number> = {
  unknown_source: 404, unknown_opportunity: 404, unknown_application: 404,
  disabled: 409, fixture_source: 409, tos_not_allowed: 409, already_queued: 409, not_dead: 409, already_exists: 409, invalid_transition: 409,
};
