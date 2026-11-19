import { eligibilityLabel, originLabel, statusLabel, auditLabel } from "@/lib/format";

const ELIG: Record<string, string> = {
  eligible: "bg-eligible-bg text-eligible border-eligible/40",
  needs_confirmation: "bg-confirm-bg text-confirm border-confirm/40",
  ineligible: "bg-inelig-bg text-inelig border-inelig/40",
};

export function EligibilityBadge({ value }: { value: string }) {
  return <span className={`inline-block rounded-sm border px-2 py-0.5 text-[13px] font-semibold ${ELIG[value] ?? "border-rule"}`}>{eligibilityLabel(value)}</span>;
}

/** Origem sempre visível: teste e desconhecido são impossíveis de confundir com dado real. */
export function OriginBadge({ value }: { value: string }) {
  if (value === "real") return <span className="inline-block rounded-sm border border-rule px-2 py-0.5 text-[13px] text-ink-soft">{originLabel(value)}</span>;
  if (value === "fixture") return <span className="hatch-test inline-block rounded-sm border border-test bg-test-bg px-2 py-0.5 text-[13px] font-bold text-test">TESTE · não é pesquisa real</span>;
  return <span className="dashed-unknown inline-block rounded-sm px-2 py-0.5 text-[13px] font-semibold text-ink-soft">{originLabel(value)}</span>;
}

export function StatusBadge({ value }: { value: string }) {
  const tone = value === "open" ? "text-eligible" : value === "closed" ? "text-ink-soft line-through" : "text-confirm";
  return <span className={`text-[13px] font-semibold ${tone}`}>{statusLabel(value)}</span>;
}

export function AuditBadge({ value }: { value: string }) {
  if (value === "not_requested") return null;
  const tone = value === "disputed" ? "text-inelig" : value === "confirmed" ? "text-eligible" : "text-ink-soft";
  return <span className={`text-[13px] ${tone}`}>{auditLabel(value)}</span>;
}
