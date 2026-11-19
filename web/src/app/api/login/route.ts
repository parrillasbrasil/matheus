import { LoginLimiter, SESSION_COOKIE, SESSION_TTL_SECONDS, safeNext, signSession, verifyPassword } from "@/lib/auth";
import { authConfig, readBody, sameOrigin, wantsJson } from "@/lib/http";

const g = globalThis as unknown as { __loginLimiters?: { ip: LoginLimiter; all: LoginLimiter } };
// Por IP (5 em 15 min) e global (50 em 15 min): o global cobre quem forja X-Forwarded-For.
const limiters = (g.__loginLimiters ??= { ip: new LoginLimiter(5, 15 * 60_000), all: new LoginLimiter(50, 15 * 60_000) });

const clientIp = (req: Request) => (req.headers.get("x-forwarded-for") ?? "").split(",")[0].trim() || "unknown";

export async function POST(req: Request): Promise<Response> {
  const cfg = authConfig();
  if (cfg instanceof Response) return cfg;
  const json = wantsJson(req);
  const body = await readBody(req);
  const back = (code: string, status: number) => json ? Response.json({ error: code }, { status })
    : new Response(null, { status: 303, headers: { location: `/login?error=${code}&next=${encodeURIComponent(safeNext(body.next))}` } });
  if (!sameOrigin(req)) return Response.json({ error: "cross_origin_blocked" }, { status: 403 });

  const now = Date.now();
  const ip = clientIp(req);
  if (!limiters.ip.allowed(ip, now) || !limiters.all.allowed("*", now)) return back("too_many_attempts", 429);

  // Sempre verifica a senha, mesmo com usuário errado: o tempo de resposta não revela se o usuário existe.
  const passwordOk = await verifyPassword(body.password ?? "", cfg.passwordHash);
  if (!(passwordOk && body.username === cfg.user)) {
    limiters.ip.fail(ip, now); limiters.all.fail("*", now);
    return back("invalid_credentials", 401);
  }
  limiters.ip.reset(ip);
  const token = await signSession(cfg.user, cfg.secret, now);
  const secure = process.env.COOKIE_SECURE !== "false";
  const cookie = `${SESSION_COOKIE}=${token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${SESSION_TTL_SECONDS}${secure ? "; Secure" : ""}`;
  if (json) return Response.json({ ok: true }, { headers: { "set-cookie": cookie } });
  return new Response(null, { status: 303, headers: { location: safeNext(body.next), "set-cookie": cookie } });
}
