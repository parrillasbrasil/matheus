// Formatação e rótulos em português. Regra de ouro: o que a fonte não disse aparece como "desconhecido", nunca como zero ou palpite.

type Money = { amount: string | number | null; currency: string | null; period: string | null; gross_net: string | null };

const PERIOD: Record<string, string> = { one_time: "pago uma vez", monthly: "por mês", yearly: "por ano", hourly: "por hora", total: "total" };
const GROSS_NET: Record<string, string> = { net: "líquido", gross: "bruto" };

export function formatMoney(m: Money): string {
  const n = m.amount === null ? NaN : Number(m.amount);
  if (!Number.isFinite(n) || !m.currency) return "desconhecido";
  const digits = Number.isInteger(n) ? 0 : 2;
  const num = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
  return [`${m.currency} ${num}`, PERIOD[m.period ?? ""] ?? "período não informado", GROSS_NET[m.gross_net ?? ""] ?? "bruto/líquido não informado"]
    .join(" · ");
}

type Deadline = { deadline_at: string | Date | null; deadline_tz: string | null; deadline_confirmed: boolean; deadline_tz_assumed: boolean };

function parts(d: Date, tz: string): string {
  const f = new Intl.DateTimeFormat("pt-BR", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const p = Object.fromEntries(f.formatToParts(d).map((x) => [x.type, x.value]));
  return `${p.day}/${p.month}/${p.year} ${p.hour}:${p.minute}`;
}

// Aceita a linha do banco (Row) ou um objeto já tipado.
export function formatDeadline(o: Deadline | Record<string, any>): { text: string; confirmed: boolean } {   // eslint-disable-line @typescript-eslint/no-explicit-any
  const { deadline_at, deadline_tz, deadline_confirmed, deadline_tz_assumed } = o as Deadline;
  if (!deadline_at) return { text: "desconhecido", confirmed: false };
  const d = new Date(deadline_at);
  if (!deadline_tz) return { text: `${parts(d, "UTC")} UTC · fuso não informado pela fonte`, confirmed: deadline_confirmed };
  let shown: string;
  try { shown = parts(d, deadline_tz); } catch { return { text: `${parts(d, "UTC")} UTC · fuso inválido (${deadline_tz})`, confirmed: false }; }
  return { text: `${shown} (${deadline_tz})${deadline_tz_assumed ? " · fuso assumido, não informado pela fonte" : ""}`, confirmed: deadline_confirmed };
}

export function daysUntil(iso: string | Date | null, now: Date): number | null {
  if (!iso) return null;
  return Math.ceil((new Date(iso).getTime() - now.getTime()) / 86_400_000);
}

const L = (map: Record<string, string>) => (k: string | null | undefined) => (k && map[k]) || k || "desconhecido";
export const eligibilityLabel = L({ eligible: "Elegível", needs_confirmation: "Precisa confirmar", ineligible: "Inelegível" });
export const originLabel = L({ real: "Dado real", fixture: "Teste (fixture)", unknown: "Origem desconhecida" });
export const statusLabel = L({ open: "Aberta", closed: "Encerrada", unknown: "Situação desconhecida" });
export const resultLabel = L({ pass: "Atende", fail: "Não atende", unknown: "A confirmar", info: "Informativo" });
export const relocationLabel = L({ none: "Sem mudança prévia", before_apply: "Mudar antes de se candidatar", after_award: "Mudança só depois da concessão" });
export const auditLabel = L({ not_requested: "Sem auditoria", confirmed: "Auditoria confirmou", disputed: "Auditoria contestou", unavailable: "Auditoria indisponível" });
export const categoryLabel = L({
  gov_relocation_incentive: "Incentivo governamental para novos moradores", job_with_package: "Emprego com pacote de mudança",
  scholarship_or_vocational: "Bolsa ou curso profissionalizante", youth_program_covered: "Programa para jovens com despesas cobertas",
  other_cost_reduction: "Outra redução de custos", unknown: "Categoria desconhecida" });
export const benefitLabel = L({ relocation_grant: "Auxílio de mudança", salary_job: "Emprego com salário", housing_support: "Moradia", travel_covered: "Passagens", scholarship: "Bolsa",
  vocational_training: "Curso profissionalizante", paid_program: "Programa remunerado", youth_program_covered: "Programa para jovens", other: "Outro" });
export const sourceStatusLabel = L({ success: "Sucesso", partial: "Parcial", failed: "Falhou", blocked_by_robots: "Bloqueada pelo robots.txt", blocked_by_tos: "Bloqueada pelos termos de uso", skipped: "Ignorada", running: "Em execução" });
export const tosLabel = L({ allowed: "Termos aprovados", pending_review: "Termos pendentes de revisão", forbidden: "Coleta proibida" });
export const applicationLabel = L({ draft: "Rascunho", awaiting_human_approval: "Aguardando aprovação humana", approved_by_human: "Aprovada por uma pessoa", submitted_by_human: "Enviada por uma pessoa", withdrawn: "Retirada" });

const NOTICES: Record<string, string> = {
  scan_queued: "Coleta colocada na fila. O worker executa respeitando termos de uso, robots.txt e limites da fonte.",
  job_requeued: "Job devolvido à fila.",
  application_created: "Rascunho de candidatura criado. Nada foi enviado.",
  application_awaiting_human_approval: "Candidatura marcada como aguardando aprovação.",
  application_approved_by_human: "Aprovação registrada em seu nome.",
  application_submitted_by_human: "Registrado que você enviou a candidatura por conta própria.",
  application_withdrawn: "Candidatura retirada.",
  unknown_source: "Fonte não encontrada.", disabled: "A fonte está desabilitada no config.yaml.", fixture_source: "Fontes de teste não rodam no painel.",
  tos_not_allowed: "Os termos de uso desta fonte ainda não foram aprovados; a coleta não roda.", already_queued: "Já existe uma coleta na fila para esta fonte.",
  not_dead: "O job não está na fila de falhas.", already_exists: "Já existe uma candidatura ativa para esta oportunidade.",
  invalid_transition: "Essa mudança de etapa não é permitida.", unknown_opportunity: "Oportunidade não encontrada.", unknown_application: "Candidatura não encontrada.",
  source_id_required: "Informe a fonte.", invalid_status: "Etapa inválida.", invalid_job_id: "Job inválido.",
};
export const noticeText = (code: string | undefined | null): string | null => (code && Object.hasOwn(NOTICES, code) ? NOTICES[code] : null);
