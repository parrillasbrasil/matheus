import type { Metadata } from "next";
import { getPool } from "@/lib/db";
import { usageSummary } from "@/lib/queries";
import { requirePageUser } from "@/lib/session";
import { Empty, PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Custos de IA" };
const n = (v: number) => new Intl.NumberFormat("pt-BR").format(v);
const usd = (v: string | number) => `US$ ${Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;

export default async function Costs() {
  await requirePageUser();
  const u = await usageSummary(getPool(), new Date());
  const block = (title: string, s: typeof u.today) => (
    <section className="border border-rule bg-sheet p-4">
      <h2 className="mb-2 text-lg font-semibold">{title}</h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-ink-soft">Chamadas</dt><dd>{n(s.calls)} ({n(s.cached_calls)} atendidas pelo cache, sem custo)</dd>
        <dt className="text-ink-soft">Tokens de entrada</dt><dd>{n(s.input_tokens)}</dd>
        <dt className="text-ink-soft">Tokens de saída</dt><dd>{n(s.output_tokens)}</dd>
        <dt className="text-ink-soft">Custo conhecido</dt><dd>{usd(s.known_cost_usd)}</dd>
        <dt className="text-ink-soft">Custo desconhecido</dt><dd className={s.unknown_cost_calls ? "font-semibold text-confirm" : ""}>{s.unknown_cost_calls ? `${n(s.unknown_cost_calls)} chamada(s) de modelo sem preço no config.yaml` : "nenhuma"}</dd>
      </dl>
    </section>);
  return (
    <>
      <PageTitle title="Custos de IA" hint="Tokens sempre são contados. Quando o modelo não tem preço no config.yaml, o custo aparece como desconhecido em vez de zero." />
      <div className="grid gap-4 md:grid-cols-2">{block("Hoje (UTC)", u.today)}{block("Últimos 30 dias", u.last30)}</div>
      {u.budgetBlocked30d > 0 && <p className="mt-4 border-l-4 border-confirm bg-confirm-bg px-4 py-2 text-confirm" role="status">{u.budgetBlocked30d} chamada(s) foram bloqueadas pelo limite diário de tokens ou custo. Aumente o limite no config.yaml se quiser processar mais.</p>}
      <h2 className="mb-2 mt-10 text-xl font-semibold">Por modelo (30 dias)</h2>
      {u.byModel.length === 0 ? <Empty>Nenhuma chamada de IA registrada. Sem chaves da OpenAI e da Anthropic o radar guarda os documentos e espera (veja o README).</Empty> : (
        <div className="overflow-x-auto border border-rule bg-sheet"><table className="tbl w-full min-w-[640px]">
          <thead><tr><th>Provedor</th><th>Modelo</th><th>Finalidade</th><th>Chamadas</th><th>Entrada</th><th>Saída</th><th>Custo conhecido</th></tr></thead>
          <tbody>{u.byModel.map((m, i) => (<tr key={i}><td>{m.provider}</td><td>{m.model}</td><td>{m.purpose === "extraction" ? "Extração" : "Auditoria"}</td><td>{n(m.calls)}</td><td>{n(m.input_tokens)}</td><td>{n(m.output_tokens)}</td><td>{usd(m.known_cost_usd)}{m.unknown_cost_calls ? ` + ${m.unknown_cost_calls} sem preço` : ""}</td></tr>))}</tbody></table></div>)}
    </>
  );
}
