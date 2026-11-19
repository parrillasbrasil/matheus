import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { assertAuthConfig, isAuthConfigured, SESSION_COOKIE, verifySession } from "./auth";

/** Para páginas: devolve o usuário logado ou manda para /login. Ler cookies() primeiro torna a página dinâmica (nunca pré-renderizada no build). */
export async function requirePageUser(): Promise<string> {
  const store = await cookies();
  if (!isAuthConfigured(process.env)) return "admin (preview)";
  const cfg = assertAuthConfig(process.env);
  const s = await verifySession(store.get(SESSION_COOKIE)?.value, cfg.secret, Date.now());
  if (!s) redirect("/login");
  return s.user;
}
