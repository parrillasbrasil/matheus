import Link from "next/link";
import type { Metadata } from "next";
import { getPool } from "@/lib/db";
import { overview } from "@/lib/queries";
import { requirePageUser } from "@/lib/session";
import { daysUntil, eligibilityLabel, formatDeadline, formatMoney } from "@/lib/format";
import { Empty, Notice, PageTitle } from "@/components/ui";
import { EligibilityBadge, OriginBadge } from "@/components/badges";

export const metadata: Metadata = { title: "Visão geral" };
const LANES = ["eligible", "needs_confirmation", "ineligible"] as const;
const LANE_STYLE = { eligible: "border-eligible text-eligible", needs_confirmation: "border-confirm text-confirm", ineligible: "border-inelig text-inelig" } as const;

export default async function Home({ searchParams }: { searchParams: Promise<{ notice?: string; error?: string }> }) {
  await requirePageUser();
  const sp = await searchParams;
  const now = new Date();
  const o = await overview(getPool(), now);
  const realTotal = LANES.reduce((n, l) => n + o.lanes.real[l], 0);
  const w = o.worker;
  const workerText = w.state === "ok" ? `Coletor ativo (último sinal há ${w.age_seconds}s).`
    : w.state === "stale" ? `Sem sinal do coletor há ${Math.round((w.age_seconds ?? 0) / 60)} min. Verifique o serviço worker no Coolify.`
    : "O coletor ainda não deu sinal de vida. Suba o serviço worker (veja o README).";

  return (
    <>
      <PageTitle title="Visão geral" hint="Oportunidades separadas pela avaliação de elegibilidade e pela origem do dado." />
      <Notice notice={sp.notice} error={sp.error} />
      <p className={`mb-6 border-l-4 px-4 py-2 ${w.state === "ok" ? "border-eligible bg-eligible-bg text-eligible" : "border-confirm bg-confirm-bg text-confirm"}`} role="status">{workerText}</p>

      <section aria-labelledby="faixas" className="mb-10">
        <h2 id="faixas" className="mb-3 text-xl font-semibold">Elegibilidade por origem do dado</h2>
        <div className="border border-rule bg-sheet">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className="w-28 p-2 text-left text-sm font-semibold text-ink-soft sm:w-44 sm:p-3">Origem</th>
                {LANES.map((l) => <th key={l} className={`border-b-4 p-2 text-left text-xs font-semibold sm:p-3 sm:text-sm ${LANE_STYLE[l]}`}>{eligibilityLabel(l)}</th>)}
              </tr>
            </thead>
            <tbody>
              {(["real", "fixture", "unknown"] as const).map((origin) => (
                <tr key={origin} className={origin === "fixture" ? "hatch-test" : ""}>
                  <th scope="row" className="p-2 text-left align-middle font-normal sm:p-3"><OriginBadge value={origin} /></th>
                  {LANES.map((l) => {
                    const n = o.lanes[origin][l];
                    return (
                      <td key={l} className="p-2 sm:p-3">
                        <Link href={`/opportunities?origin=${origin}&eligibility=${l}`} className={`font-display no-underline ${origin === "real" ? "text-4xl font-semibold sm:text-5xl" : "text-2xl"} ${n ? LANE_STYLE[l].split(" ")[1] : "text-ink-soft"}`}>{n}</Link>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {realTotal === 0 && (
          <div className="mt-4"><Empty>Nenhuma oportunidade real ainda. O radar só registra oportunidades depois de ler uma fonte oficial. Confira em <Link href="/sources">Fontes</Link> quais já podem rodar e peça uma coleta.</Empty></div>
        )}
      </section>

      <section aria-labelledby="prazos" className="mb-10">
        <h2 id="prazos" className="mb-3 text-xl font-semibold">Prazos confirmados nos próximos 14 dias</h2>
        {o.closingSoon.length === 0 ? <Empty>Nenhuma oportunidade aberta com prazo confirmado nesse período.</Empty> : (
          <ul className="divide-y divide-rule-soft border border-rule bg-sheet">
            {o.closingSoon.map((r) => {
              const d = daysUntil(r.deadline_at, now);
              return (
                <li key={r.id} className={`flex flex-wrap items-baseline gap-x-4 gap-y-1 px-4 py-3 ${r.data_origin === "fixture" ? "hatch-test" : ""}`}>
                  <span className="font-display text-2xl font-semibold tabular-nums">{d === 0 ? "hoje" : d === 1 ? "1 dia" : `${d} dias`}</span>
                  <Link href={`/opportunities/${r.id}`} className="font-semibold">{r.title_pt ?? r.title_original}</Link>
                  <EligibilityBadge value={r.eligibility_status} />
                  <OriginBadge value={r.data_origin} />
                  <span className="text-sm text-ink-soft">{formatDeadline(r)?.text} · {formatMoney({ amount: r.value_amount, currency: r.value_currency, period: r.value_period, gross_net: r.value_gross_net })}</span>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section aria-labelledby="coleta">
        <h2 id="coleta" className="mb-3 text-xl font-semibold">Estado da coleta</h2>
        <dl className="grid gap-px border border-rule bg-rule sm:grid-cols-2 lg:grid-cols-4">
          {[
            { k: "Fontes reais prontas para rodar", v: o.sources.active, href: "/sources" },
            { k: "Fontes com falhas seguidas", v: o.sources.failing, href: "/sources" },
            { k: "Fontes aguardando revisão dos termos de uso", v: o.sources.pending_tos, href: "/sources" },
            { k: "Jobs na fila de falhas", v: o.deadJobs, href: "/jobs" },
          ].map((x) => (
            <div key={x.k} className="bg-sheet p-4"><dt className="text-sm text-ink-soft">{x.k}</dt><dd className="mt-1"><Link href={x.href} className="font-display text-3xl font-semibold no-underline">{x.v}</Link></dd></div>
          ))}
        </dl>
      </section>
    </>
  );
}
