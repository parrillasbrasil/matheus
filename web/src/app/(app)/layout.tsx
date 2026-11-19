import { Nav } from "@/components/nav";
import { requirePageUser } from "@/lib/session";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const user = await requirePageUser();
  return (
    <div className="mx-auto grid max-w-[1280px] grid-cols-[minmax(0,1fr)] gap-6 px-4 py-6 md:grid-cols-[220px_minmax(0,1fr)]">
      <aside className="md:sticky md:top-6 md:self-start">
        <p className="mb-4 font-display text-xl font-semibold leading-tight">Radar de<br />oportunidades</p>
        <Nav />
        <form method="post" action="/api/logout" className="mt-6 text-sm text-ink-soft">
          <p className="mb-2">Entrou como {user}</p>
          <button type="submit" className="btn btn-quiet">Sair</button>
        </form>
      </aside>
      <main id="conteudo" className="min-w-0">{children}</main>
    </div>
  );
}
