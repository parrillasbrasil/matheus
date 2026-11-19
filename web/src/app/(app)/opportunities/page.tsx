import Link from "next/link";
import type { Metadata } from "next";
import { getPool } from "@/lib/db";
import { CATEGORIES, ELIGIBILITIES, ORIGINS, parseFilters, SORTS, STATUSES } from "@/lib/filters";
import { listOpportunities } from "@/lib/queries";
import { requirePageUser } from "@/lib/session";
import { categoryLabel, eligibilityLabel, formatDeadline, formatMoney, originLabel, statusLabel } from "@/lib/format";
import { Empty, Notice, PageTitle, searchParamsToString } from "@/components/ui";
import { AuditBadge, EligibilityBadge, OriginBadge, StatusBadge } from "@/components/badges";

export const metadata: Metadata = { title: "Oportunidades" };
const SORT_LABEL = { score: "Maior pontuação", deadline: "Prazo mais próximo", updated: "Atualizadas há pouco" } as const;
const BAR = { eligible: "border-l-eligible", needs_confirmation: "border-l-confirm", ineligible: "border-l-inelig" } as const;

function Select({ name, value, options, label, all = "Todos", wide = false }: { name: string; value: string | null; options: readonly [string, string][]; label: string; all?: string; wide?: boolean }) {
  return (
    <label className={`grid gap-1 text-sm ${wide ? "col-span-2 lg:col-span-1" : ""}`}><span className="font-semibold">{label}</span>
      <select name={name} defaultValue={value ?? ""} className="field">
        <option value="">{all}</option>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}

export default async function Opportunities({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  await requirePageUser();
  const sp = await searchParams;
  const f = parseFilters(sp);
  const { rows, hasNext } = await listOpportunities(getPool(), f);
  const base = searchParamsToString(sp, ["page", "notice", "error"]);
  const pageHref = (p: number) => `/opportunities?${base}${base ? "&" : ""}page=${p}`;

  return (
    <>
      <PageTitle title="Oportunidades" hint="Cada linha mostra a avaliação de elegibilidade, a origem do dado e quantas citações da fonte oficial foram conferidas literalmente." />
      <Notice notice={typeof sp.notice === "string" ? sp.notice : undefined} error={typeof sp.error === "string" ? sp.error : undefined} />
      <form method="get" className="mb-6 grid grid-cols-2 gap-3 border border-rule bg-sheet p-4 lg:grid-cols-4">
        <Select name="eligibility" label="Elegibilidade" value={f.eligibility} options={ELIGIBILITIES.map((v) => [v, eligibilityLabel(v)])} />
        <Select name="status" label="Situação" value={f.status} options={STATUSES.map((v) => [v, statusLabel(v)])} />
        <Select name="origin" label="Origem do dado" value={f.origin} options={ORIGINS.map((v) => [v, originLabel(v)])} />
        <Select wide name="category" label="Categoria" value={f.category} options={CATEGORIES.map((v) => [v, categoryLabel(v)])} />
        <label className="grid gap-1 text-sm"><span className="font-semibold">País (código de 2 letras)</span><input name="country" defaultValue={f.country ?? ""} maxLength={2} className="field" placeholder="IT" /></label>
        <label className="grid gap-1 text-sm"><span className="font-semibold">Pontuação mínima</span><input name="min_score" type="number" min={0} max={100} defaultValue={f.minScore ?? ""} className="field" /></label>
        <label className="col-span-2 grid gap-1 text-sm lg:col-span-1"><span className="font-semibold">Buscar no título ou emissor</span><input name="q" defaultValue={f.q ?? ""} className="field" /></label>
        <Select name="sort" label="Ordenar por" value={f.sort} all="Maior pontuação" options={SORTS.filter((s) => s !== "score").map((v) => [v, SORT_LABEL[v]])} />
        <div className="col-span-2 flex items-end gap-2 lg:col-span-4">
          <button type="submit" className="btn">Filtrar</button>
          <Link href="/opportunities" className="btn btn-quiet no-underline">Limpar filtros</Link>
        </div>
      </form>

      {rows.length === 0 ? <Empty>Nenhuma oportunidade com esses filtros. {base ? <Link href="/opportunities">Limpar filtros</Link> : "O radar ainda não registrou oportunidades: peça uma coleta em Fontes."}</Empty> : (
        <ul className="grid gap-3">
          {rows.map((r) => {
            const dl = formatDeadline(r);
            return (
              <li key={r.id} className={`border border-rule border-l-8 bg-sheet p-4 ${BAR[r.eligibility_status as keyof typeof BAR] ?? "border-l-rule"} ${r.data_origin === "fixture" ? "hatch-test" : ""} ${r.data_origin === "unknown" ? "dashed-unknown" : ""}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <EligibilityBadge value={r.eligibility_status} /><OriginBadge value={r.data_origin} /><StatusBadge value={r.status} /><AuditBadge value={r.audit_status} />
                </div>
                <h2 className="mt-2 text-lg font-semibold"><Link href={`/opportunities/${r.id}`}>{r.title_pt ?? r.title_original ?? "Sem título"}</Link></h2>
                <p className="text-sm text-ink-soft">{[r.issuer, r.city_or_region, r.country].filter(Boolean).join(", ") || "Emissor desconhecido"} · {categoryLabel(r.category)}</p>
                <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
                  <div><dt className="text-ink-soft">Benefício</dt><dd>{formatMoney({ amount: r.value_amount, currency: r.value_currency, period: r.value_period, gross_net: r.value_gross_net })}</dd></div>
                  <div><dt className="text-ink-soft">Prazo{dl.confirmed ? " confirmado" : ""}</dt><dd className={dl.confirmed ? "font-semibold" : ""}>{dl.text}</dd></div>
                  <div><dt className="text-ink-soft">Citações conferidas</dt><dd>{r.evidence_verified} de {r.evidence_total}</dd></div>
                  <div><dt className="text-ink-soft">Pontuação</dt><dd>{r.current_score === null ? "sem pontuação" : Number(r.current_score).toFixed(0)}</dd></div>
                </dl>
                <p className="mt-2 text-xs text-ink-soft">Verificada em {new Date(r.last_verified_at).toLocaleString("pt-BR", { timeZone: "UTC" })} UTC{!r.is_official && " · fonte NÃO oficial"}</p>
              </li>
            );
          })}
        </ul>
      )}
      <div className="mt-6 flex gap-3">
        {f.page > 1 && <Link className="btn btn-quiet no-underline" href={pageHref(f.page - 1)}>Página anterior</Link>}
        {hasNext && <Link className="btn btn-quiet no-underline" href={pageHref(f.page + 1)}>Próxima página</Link>}
      </div>
    </>
  );
}
