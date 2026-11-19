import { SESSION_COOKIE } from "@/lib/auth";
import { sameOrigin, wantsJson } from "@/lib/http";

export async function POST(req: Request): Promise<Response> {
  if (!sameOrigin(req)) return Response.json({ error: "cross_origin_blocked" }, { status: 403 });
  const secure = process.env.COOKIE_SECURE !== "false";
  const clear = `${SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0${secure ? "; Secure" : ""}`;
  if (wantsJson(req)) return Response.json({ ok: true }, { headers: { "set-cookie": clear } });
  return new Response(null, { status: 303, headers: { location: "/login", "set-cookie": clear } });
}
