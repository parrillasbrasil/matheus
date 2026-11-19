import type { Metadata } from "next";
import { safeNext } from "@/lib/auth";

export const metadata: Metadata = { title: "Entrar" };
const ERRORS: Record<string, string> = {
  invalid_credentials: "Usuário ou senha incorretos.",
  too_many_attempts: "Muitas tentativas. Aguarde 15 minutos e tente de novo.",
};

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ error?: string; next?: string }> }) {
  const { error, next } = await searchParams;
  return (
    <main className="mx-auto grid min-h-screen max-w-md content-center px-4">
      <h1 className="mb-1 text-3xl font-semibold">Radar de oportunidades</h1>
      <p className="mb-6 text-ink-soft">Painel privado. Entre para ver as oportunidades verificadas.</p>
      {error && Object.hasOwn(ERRORS, error) && <p role="alert" className="mb-4 border-l-4 border-inelig bg-inelig-bg px-4 py-2 text-inelig">{ERRORS[error]}</p>}
      <form method="post" action="/api/login" className="grid gap-4">
        <input type="hidden" name="next" value={safeNext(next)} />
        <label className="grid gap-1"><span className="font-semibold">Usuário</span>
          <input name="username" autoComplete="username" required className="field" /></label>
        <label className="grid gap-1"><span className="font-semibold">Senha</span>
          <input name="password" type="password" autoComplete="current-password" required className="field" /></label>
        <button type="submit" className="btn justify-center">Entrar</button>
      </form>
    </main>
  );
}
