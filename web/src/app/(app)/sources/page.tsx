import type { Metadata } from "next";
import { getPool } from "@/lib/db";
import { listRecentRuns, listSources } from "@/lib/queries";
import { requirePageUser } from "@/lib/session";
import { sourceStatusLabel, tosLabel } from "@/lib/format";
import { Empty, Notice, PageTitle, PostButton } from "@/components/ui";
import { OriginBadge } from "@/components/badges";

export const metadata: Metadata = { title: "Fontes" };
const when = (v: string | Date | null) => (v ? new Date(v).toLocaleString("pt-BR", { timeZone: "UTC" }) + " UTC" : "nunca");

export default async function Sources({ searchParams }: { searchParams: Promise<{ notice?: string; error?: string }> }) {
  await requirePageUser();
  const sp = await searchParams;
  const [sources, runs] = await Promise.all([listSources(getPool()), listRecentRuns(getPool())]);
  return (
    <>
      <PageTitle title="Fontes" hint="Uma fonte só é coletada com termos de uso aprovados no config.yaml. Fontes com termos pendentes ou proibidos nunca rodam, nem manualmente." />
      <Notice notice={sp.notice} error={sp.error} />
      {sources.length === 0 ? <Empty>Nenhuma fonte cadastrada. Rode <code>radar sync</code> para carregar o config.yaml.</Empty> : (
        <div className="overflow-x-auto border border-rule bg-sheet"><table className="tbl w-full min-w-[860px]">
          <thead><tr><th>Fonte</th><th>Termos de uso</th><th>Última coleta</th><th>Falhas seguidas</th><th>Próxima</th><th>Ação</th></tr></thead>
          <tbody>{sources.map((s) => {
            const runnable = s.enabled && s.origin === "real" && s.tos_status === "allowed";
            return (
              <tr key={s.id} className={s.origin === "fixture" ? "hatch-test" : ""}>
                <td><p className="font-semibold">{s.name}</p><p className="text-sm text-ink-soft">{s.id} · {s.country ?? "—"} · {s.opportunities} oportunidade(s)</p><OriginBadge value={s.origin} />{!s.enabled && <span className="ml-2 text-sm text-ink-soft">desabilitada</span>}</td>
                <td><span className={s.tos_status === "allowed" ? "font-semibold text-eligible" : "font-semibold text-confirm"}>{tosLabel(s.tos_status)}</span>{s.tos_note && <p className="max-w-[34ch] text-sm text-ink-soft">{s.tos_note}</p>}</td>
                <td>{s.last_run_status ? <><span className={s.last_run_status === "success" ? "text-eligible" : "text-inelig"}>{sourceStatusLabel(s.last_run_status)}</span><p className="text-sm text-ink-soft">{when(s.last_run_started_at)}</p>{s.last_run_error && <p className="max-w-[34ch] text-sm text-inelig">{s.last_run_error}</p>}</> : "nunca coletada"}</td>
                <td className={s.consecutive_failures ? "font-semibold text-inelig" : ""}>{s.consecutive_failures}</td>
                <td className="text-sm">{when(s.next_run_at)}</td>
                <td>{runnable ? <PostButton action="/api/scan" fields={{ source_id: s.id }} next="/sources" quiet>Coletar agora</PostButton> : <span className="text-sm text-ink-soft">{s.origin === "fixture" ? "Fonte de teste" : !s.enabled ? "Habilite no config.yaml" : "Aprove os termos no config.yaml"}</span>}</td>
              </tr>);
          })}</tbody></table></div>)}
      <h2 className="mb-2 mt-10 text-xl font-semibold">Últimas coletas</h2>
      {runs.length === 0 ? <Empty>Nenhuma coleta registrada ainda.</Empty> : (
        <div className="overflow-x-auto border border-rule bg-sheet"><table className="tbl w-full min-w-[720px]">
          <thead><tr><th>Quando</th><th>Fonte</th><th>Resultado</th><th>Vistos</th><th>Novos</th><th>Alterados</th><th>Iguais</th><th>Com erro</th></tr></thead>
          <tbody>{runs.map((r) => (<tr key={r.id}><td className="text-sm">{when(r.started_at)}</td><td>{r.source_id}</td><td>{sourceStatusLabel(r.status)}{r.trigger === "manual" && <span className="text-sm text-ink-soft"> (manual)</span>}{r.error && <p className="max-w-[40ch] text-sm text-inelig">{r.error}</p>}</td><td>{r.items_seen}</td><td>{r.items_new}</td><td>{r.items_changed}</td><td>{r.items_unchanged}</td><td>{r.items_failed}</td></tr>))}</tbody></table></div>)}
    </>
  );
}
