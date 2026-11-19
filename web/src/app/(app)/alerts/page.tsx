import Link from "next/link";
import type { Metadata } from "next";
import { getPool } from "@/lib/db";
import { listAlerts } from "@/lib/queries";
import { requirePageUser } from "@/lib/session";
import { Empty, PageTitle } from "@/components/ui";
import { OriginBadge } from "@/components/badges";

export const metadata: Metadata = { title: "Alertas" };
const KIND: Record<string, string> = { new: "Nova oportunidade", changed: "Oportunidade alterada", urgent: "Urgente: prazo confirmado perto" };
const STATUS: Record<string, string> = { pending: "Aguardando envio (ou credenciais do canal)", sent: "Enviado", failed: "Falhou", suppressed: "Suprimido" };

export default async function Alerts() {
  await requirePageUser();
  const rows = await listAlerts(getPool());
  return (
    <>
      <PageTitle title="Alertas" hint="Alertas só nascem de oportunidades elegíveis ou a confirmar, com evidência verificada. Oportunidades encerradas, vencidas ou inelegíveis são suprimidas, e o motivo fica registrado." />
      {rows.length === 0 ? <Empty>Nenhum alerta registrado ainda.</Empty> : (
        <div className="overflow-x-auto border border-rule bg-sheet"><table className="tbl w-full min-w-[720px]">
          <thead><tr><th>Quando</th><th>Oportunidade</th><th>Tipo</th><th>Canal</th><th>Estado</th></tr></thead>
          <tbody>{rows.map((a) => (
            <tr key={a.id} className={a.data_origin === "fixture" ? "hatch-test" : ""}>
              <td className="text-sm">{new Date(a.created_at).toLocaleString("pt-BR", { timeZone: "UTC" })} UTC</td>
              <td><Link href={`/opportunities/${a.opportunity_id}`}>{a.title_original ?? "Sem título"}</Link><div className="mt-1"><OriginBadge value={a.data_origin} /></div></td>
              <td>{KIND[a.kind] ?? a.kind}</td><td>{a.channel === "smtp" ? "E-mail" : "Telegram"}</td>
              <td>{STATUS[a.status] ?? a.status}{a.suppression_reason && <p className="text-sm text-ink-soft">{a.suppression_reason}</p>}{a.last_error && <p className="max-w-[36ch] text-sm text-inelig">{a.last_error}</p>}{a.attempts > 0 && <p className="text-sm text-ink-soft">{a.attempts} tentativa(s)</p>}</td>
            </tr>))}</tbody></table></div>)}
    </>
  );
}
