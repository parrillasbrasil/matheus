import Link from "next/link";
import type { Metadata } from "next";
import { getPool } from "@/lib/db";
import { listApplications } from "@/lib/queries";
import { requirePageUser } from "@/lib/session";
import { applicationLabel } from "@/lib/format";
import { Empty, Notice, PageTitle, PostButton } from "@/components/ui";
import { OriginBadge } from "@/components/badges";

export const metadata: Metadata = { title: "Candidaturas" };
const NEXT: Record<string, { to: string; label: string }[]> = {
  draft: [{ to: "awaiting_human_approval", label: "Marcar como aguardando aprovação" }, { to: "withdrawn", label: "Retirar" }],
  awaiting_human_approval: [{ to: "approved_by_human", label: "Aprovar (em meu nome)" }, { to: "withdrawn", label: "Retirar" }],
  approved_by_human: [{ to: "submitted_by_human", label: "Registrar que eu enviei" }, { to: "withdrawn", label: "Retirar" }],
};

export default async function Applications({ searchParams }: { searchParams: Promise<{ notice?: string; error?: string }> }) {
  await requirePageUser();
  const sp = await searchParams;
  const rows = await listApplications(getPool());
  return (
    <>
      <PageTitle title="Candidaturas" hint="O radar nunca envia candidaturas. Aqui ficam os rascunhos e o registro de cada decisão humana: quem aprovou e quando." />
      <Notice notice={sp.notice} error={sp.error} />
      {rows.length === 0 ? <Empty>Nenhuma candidatura ainda. Abra uma oportunidade e crie um rascunho.</Empty> : (
        <ul className="grid gap-3">{rows.map((a) => (
          <li key={a.id} className={`border border-rule bg-sheet p-4 ${a.data_origin === "fixture" ? "hatch-test" : ""}`}>
            <p className="font-semibold"><Link href={`/opportunities/${a.opportunity_id}`}>{a.title_original ?? "Sem título"}</Link></p>
            <p className="mt-1 flex flex-wrap items-center gap-2"><OriginBadge value={a.data_origin} /><span>{applicationLabel(a.status)}</span>{a.approved_by && <span className="text-sm text-ink-soft">aprovada por {a.approved_by}</span>}</p>
            <div className="mt-3 flex flex-wrap gap-2">{(NEXT[a.status] ?? []).map((s) => <PostButton key={s.to} action={`/api/applications/${a.id}`} fields={{ status: s.to }} next="/applications" quiet={s.to === "withdrawn"}>{s.label}</PostButton>)}</div>
          </li>))}</ul>)}
    </>
  );
}
