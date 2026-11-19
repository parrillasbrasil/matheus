import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { getPool } from "@/lib/db";
import { getOpportunity } from "@/lib/queries";
import { requirePageUser } from "@/lib/session";
import { applicationLabel, benefitLabel, categoryLabel, formatDeadline, formatMoney, relocationLabel, resultLabel } from "@/lib/format";
import { Empty, Notice, PostButton } from "@/components/ui";
import { AuditBadge, EligibilityBadge, OriginBadge, StatusBadge } from "@/components/badges";

export const metadata: Metadata = { title: "Oportunidade" };
const RESULT_TONE: Record<string, string> = { pass: "text-eligible", fail: "text-inelig", unknown: "text-confirm", info: "text-ink-soft" };
const NEXT_STEP: Record<string, { to: string; label: string }[]> = {
  draft: [{ to: "awaiting_human_approval", label: "Marcar como aguardando aprovação" }, { to: "withdrawn", label: "Retirar" }],
  awaiting_human_approval: [{ to: "approved_by_human", label: "Aprovar (em meu nome)" }, { to: "withdrawn", label: "Retirar" }],
  approved_by_human: [{ to: "submitted_by_human", label: "Registrar que eu enviei" }, { to: "withdrawn", label: "Retirar" }],
};
type Item = { text_pt?: string; quote?: string | null };

function ItemList({ items, empty }: { items: Item[] | null; empty: string }) {
  if (!items || items.length === 0) return <p className="text-ink-soft">{empty}</p>;
  return <ul className="grid gap-2">{items.map((i, n) => <li key={n}>{i.text_pt}{i.quote && <blockquote className="mt-1 border-l-2 border-rule pl-3 text-sm text-ink-soft">“{i.quote}”</blockquote>}</li>)}</ul>;
}

export default async function OpportunityPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ notice?: string; error?: string }> }) {
  await requirePageUser();
  const [{ id }, sp] = await Promise.all([params, searchParams]);
  const d = await getOpportunity(getPool(), id);
  if (!d) notFound();
  const o = d.opportunity;
  const dl = formatDeadline(o);
  const here = `/opportunities/${id}`;
  const active = d.applications.find((a) => a.status !== "withdrawn");

  return (
    <article>
      <p className="mb-2 text-sm"><Link href="/opportunities">← Voltar à lista</Link></p>
      <Notice notice={sp.notice} error={sp.error} />
      <header className={`mb-8 border border-rule bg-sheet p-5 ${o.data_origin === "fixture" ? "hatch-test" : ""} ${o.data_origin === "unknown" ? "dashed-unknown" : ""}`}>
        <div className="flex flex-wrap items-center gap-2"><EligibilityBadge value={o.eligibility_status} /><OriginBadge value={o.data_origin} /><StatusBadge value={o.status} /><AuditBadge value={o.audit_status} /></div>
        <h1 className="mt-3 text-3xl font-semibold">{o.title_pt ?? o.title_original ?? "Sem título"}</h1>
        {o.title_original && o.title_pt && o.title_original !== o.title_pt && <p className="text-ink-soft">Título original: {o.title_original}</p>}
        <p className="mt-2 text-ink-soft">{[o.issuer, o.city_or_region, o.country].filter(Boolean).join(", ") || "Emissor desconhecido"} · {categoryLabel(o.category)}</p>
        <p className="mt-3">
          {o.is_official ? <>Fonte oficial: <a href={o.canonical_url} rel="noopener noreferrer" target="_blank">{o.canonical_url}</a></>
            : <span className="font-semibold text-inelig">Esta fonte NÃO é oficial: não use para decidir. <a href={o.canonical_url} rel="noopener noreferrer nofollow" target="_blank">{o.canonical_url}</a></span>}
        </p>
        <p className="mt-1 text-sm text-ink-soft">Coletada de “{o.source_name}” · última verificação em {new Date(o.last_verified_at).toLocaleString("pt-BR", { timeZone: "UTC" })} UTC</p>
      </header>

      <div className="grid gap-8 lg:grid-cols-2">
        <section aria-labelledby="veredito">
          <h2 id="veredito" className="mb-2 text-xl font-semibold">Elegibilidade</h2>
          <p>{o.eligibility_summary ?? "Sem resumo de elegibilidade."}</p>
          <p className="mt-2 text-sm text-ink-soft">Mudança de país: {relocationLabel(o.relocation_condition)}. Cidadania e residência são avaliadas separadamente; cidadania italiana não vira elegibilidade automática.</p>
        </section>
        <section aria-labelledby="beneficio">
          <h2 id="beneficio" className="mb-2 text-xl font-semibold">Benefício e prazo</h2>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
            <dt className="text-ink-soft">Valor</dt><dd>{formatMoney({ amount: o.value_amount, currency: o.value_currency, period: o.value_period, gross_net: o.value_gross_net })}</dd>
            <dt className="text-ink-soft">Tipo</dt><dd>{(o.benefit_types as string[]).map(benefitLabel).join(", ") || "desconhecido"}</dd>
            <dt className="text-ink-soft">Prazo</dt><dd className={dl.confirmed ? "font-semibold" : ""}>{dl.text}{dl.confirmed ? " (confirmado)" : o.deadline_at ? " (não confirmado)" : ""}</dd>
            <dt className="text-ink-soft">Resumo</dt><dd>{o.benefit_summary_pt ?? "desconhecido"}</dd>
          </dl>
        </section>
      </div>

      <section aria-labelledby="checagens" className="mt-8">
        <h2 id="checagens" className="mb-2 text-xl font-semibold">Requisitos e checagens</h2>
        {d.checks.length === 0 ? <Empty>Nenhum requisito verificável foi encontrado na fonte.</Empty> : (
          <div className="overflow-x-auto border border-rule bg-sheet"><table className="tbl w-full min-w-[640px]">
            <thead><tr><th>Resultado</th><th>Requisito</th><th>Citação da fonte</th><th>Checado por</th></tr></thead>
            <tbody>{d.checks.map((c, i) => (
              <tr key={i}><td className={`font-semibold ${RESULT_TONE[c.result]}`}>{resultLabel(c.result)}{c.fixable_by_relocation && <span className="block text-xs font-normal text-ink-soft">pode mudar após a mudança de país</span>}</td>
                <td>{c.reason_pt}</td><td className="text-sm text-ink-soft">{c.quote ? `“${c.quote}”` : "sem citação"}</td>
                <td className="text-sm text-ink-soft">{c.checked_by === "claude_audit" ? "Auditoria independente" : "Regras determinísticas"}</td></tr>))}</tbody>
          </table></div>)}
      </section>

      <section aria-labelledby="evidencias" className="mt-8">
        <h2 id="evidencias" className="mb-2 text-xl font-semibold">Evidências da fonte oficial</h2>
        {d.evidence.length === 0 ? <Empty>Sem citações registradas. Sem evidência, o radar não deveria alertar sobre esta oportunidade.</Empty> : (
          <ul className="grid gap-3">{d.evidence.map((e, i) => (
            <li key={i} className="border border-rule bg-sheet p-3"><p className="text-sm"><span className={e.verified ? "font-semibold text-eligible" : "font-semibold text-confirm"}>{e.verified ? "Conferida literalmente no texto" : "Não conferida no texto"}</span> · campo: {e.field}</p>
              <blockquote className="mt-1">“{e.quote}”</blockquote></li>))}</ul>)}
      </section>

      <div className="mt-8 grid gap-8 lg:grid-cols-2">
        <section aria-labelledby="obrig"><h2 id="obrig" className="mb-2 text-xl font-semibold">Obrigações</h2><ItemList items={o.obligations} empty="Nenhuma obrigação com citação verificada." /></section>
        <section aria-labelledby="naocob"><h2 id="naocob" className="mb-2 text-xl font-semibold">Despesas não cobertas</h2><ItemList items={o.not_covered_expenses} empty="Nenhuma despesa não coberta com citação verificada." /></section>
      </div>

      <section aria-labelledby="score" className="mt-8">
        <h2 id="score" className="mb-2 text-xl font-semibold">Pontuação {d.score ? <span className="font-display">{Number(d.score.total).toFixed(0)}</span> : "indisponível"}</h2>
        {d.score && <ul className="list-disc pl-5">{(d.score.explanation as string[]).map((x, i) => <li key={i}>{x}</li>)}</ul>}
      </section>

      <section aria-labelledby="versoes" className="mt-8">
        <h2 id="versoes" className="mb-2 text-xl font-semibold">Histórico de versões</h2>
        <ol className="grid gap-2">{d.versions.map((v) => (
          <li key={v.id} className="border border-rule bg-sheet p-3"><p className="font-semibold">Versão {v.version_no} · {new Date(v.created_at).toLocaleString("pt-BR", { timeZone: "UTC" })} UTC</p>
            {v.change_summary?.created ? <p className="text-sm text-ink-soft">Primeira leitura desta oportunidade.</p> : (
              <ul className="mt-1 text-sm">{Object.entries((v.change_summary?.fields ?? {}) as Record<string, { from: unknown; to: unknown }>).map(([k, c]) => (
                <li key={k}><span className="font-semibold">{k}</span>: {JSON.stringify(c.from)} → {JSON.stringify(c.to)}</li>))}</ul>)}</li>))}</ol>
      </section>

      <section aria-labelledby="cand" className="mt-8 border-t border-rule pt-6">
        <h2 id="cand" className="mb-2 text-xl font-semibold">Candidatura</h2>
        <p className="mb-3 max-w-[65ch] text-ink-soft">O radar nunca envia candidaturas. Aqui você registra um rascunho e cada decisão humana.</p>
        {!active ? <PostButton action="/api/applications" fields={{ opportunity_id: id }} next={here}>Criar rascunho de candidatura</PostButton> : (
          <div className="border border-rule bg-sheet p-4">
            <p className="font-semibold">{applicationLabel(active.status)}{active.approved_by && ` · aprovada por ${active.approved_by}`}</p>
            <div className="mt-3 flex flex-wrap gap-2">{(NEXT_STEP[active.status] ?? []).map((s) => (
              <PostButton key={s.to} action={`/api/applications/${active.id}`} fields={{ status: s.to }} next={here} quiet={s.to === "withdrawn"}>{s.label}</PostButton>))}</div>
          </div>)}
      </section>
    </article>
  );
}
