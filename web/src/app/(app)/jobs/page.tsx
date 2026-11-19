import type { Metadata } from "next";
import { getPool } from "@/lib/db";
import { listJobs, workerHealth } from "@/lib/queries";
import { requirePageUser } from "@/lib/session";
import { Empty, Notice, PageTitle, PostButton } from "@/components/ui";

export const metadata: Metadata = { title: "Coletas e falhas" };
const when = (v: string | Date | null) => (v ? new Date(v).toLocaleString("pt-BR", { timeZone: "UTC" }) + " UTC" : "—");

export default async function Jobs({ searchParams }: { searchParams: Promise<{ notice?: string; error?: string }> }) {
  await requirePageUser();
  const sp = await searchParams;
  const [j, w] = await Promise.all([listJobs(getPool()), workerHealth(getPool(), new Date())]);
  const tiles = [["Na fila", j.stats.queued], ["Em execução", j.stats.running], ["Concluídos", j.stats.done], ["Na fila de falhas", j.stats.dead]] as const;
  return (
    <>
      <PageTitle title="Coletas e falhas" hint="Jobs que falharam várias vezes vão para a fila de falhas e ficam aqui até alguém devolvê-los à fila. Nada é apagado." />
      <Notice notice={sp.notice} error={sp.error} />
      <p className="mb-5" role="status">Coletor: <strong>{w.state === "ok" ? "ativo" : w.state === "stale" ? "sem sinal recente" : "nunca iniciou"}</strong>{w.last_seen && ` · último sinal em ${when(w.last_seen)}`}</p>
      <dl className="mb-8 grid grid-cols-2 gap-px border border-rule bg-rule sm:grid-cols-4">{tiles.map(([k, v]) => <div key={k} className="bg-sheet p-4"><dt className="text-sm text-ink-soft">{k}</dt><dd className={`font-display text-3xl font-semibold ${k === "Na fila de falhas" && v ? "text-inelig" : ""}`}>{v}</dd></div>)}</dl>
      <h2 className="mb-2 text-xl font-semibold">Fila de falhas</h2>
      {j.dead.length === 0 ? <Empty>Nenhum job falhou definitivamente.</Empty> : (
        <div className="overflow-x-auto border border-rule bg-sheet"><table className="tbl w-full min-w-[640px]">
          <thead><tr><th>Job</th><th>Fonte</th><th>Tentativas</th><th>Último erro</th><th>Ação</th></tr></thead>
          <tbody>{j.dead.map((r) => (<tr key={r.id}><td>#{r.id} · {r.kind}<p className="text-sm text-ink-soft">{when(r.finished_at)}</p></td><td>{r.payload?.source_id ?? "—"}</td><td>{r.attempts} de {r.max_attempts}</td><td className="max-w-[44ch] text-sm text-inelig">{r.last_error ?? "sem detalhe"}</td>
            <td><PostButton action={`/api/jobs/${r.id}/requeue`} next="/jobs" quiet>Devolver à fila</PostButton></td></tr>))}</tbody></table></div>)}
      <h2 className="mb-2 mt-10 text-xl font-semibold">Fila e execução</h2>
      {j.active.length === 0 ? <Empty>Nenhum job aguardando.</Empty> : (
        <div className="overflow-x-auto border border-rule bg-sheet"><table className="tbl w-full min-w-[560px]">
          <thead><tr><th>Job</th><th>Estado</th><th>Roda em</th><th>Tentativas</th><th>Último erro</th></tr></thead>
          <tbody>{j.active.map((r) => (<tr key={r.id}><td>#{r.id} · {r.payload?.source_id ?? r.kind}</td><td>{r.status === "running" ? "Em execução" : "Na fila"}</td><td className="text-sm">{when(r.run_at)}</td><td>{r.attempts}</td><td className="max-w-[40ch] text-sm text-inelig">{r.last_error ?? ""}</td></tr>))}</tbody></table></div>)}
    </>
  );
}
