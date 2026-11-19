// Next.js 16: proxy.ts (antigo middleware). Porta de entrada: sem sessão válida, páginas vão para /login e a API responde 401.
// Cada rota e página verifica a sessão de novo (defesa em profundidade); este arquivo é só a primeira barreira.
import { NextResponse, type NextRequest } from "next/server";
import { isAuthConfigured, SESSION_COOKIE, verifySession } from "@/lib/auth";

export async function proxy(req: NextRequest) {
  if (!isAuthConfigured(process.env)) return NextResponse.next();
  const secret = process.env.SESSION_SECRET ?? "";
  const session = secret.length >= 32 ? await verifySession(req.cookies.get(SESSION_COOKIE)?.value, secret, Date.now()) : null;
  if (session) return NextResponse.next();
  if (req.nextUrl.pathname.startsWith("/api/")) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = `?next=${encodeURIComponent(req.nextUrl.pathname + req.nextUrl.search)}`;
  return NextResponse.redirect(url);
}

// Públicos: login (página e API), healthcheck, arquivos estáticos do Next.
export const config = {
  matcher: ["/((?!login|api/login|api/health|_next/static|_next/image|favicon.ico).*)"],
};
