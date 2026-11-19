// Filtros da lista de oportunidades: valida a entrada contra listas permitidas e monta SQL parametrizado.
export const DEFAULT_PAGE_SIZE = 25;

export const STATUSES = ["open", "closed", "unknown"] as const;
export const ELIGIBILITIES = ["eligible", "needs_confirmation", "ineligible"] as const;
export const ORIGINS = ["real", "fixture", "unknown"] as const;
export const CATEGORIES = ["gov_relocation_incentive", "job_with_package", "scholarship_or_vocational", "youth_program_covered", "other_cost_reduction", "unknown"] as const;
export const SORTS = ["score", "deadline", "updated"] as const;

type Pick<T extends readonly string[]> = T[number];
export type Filters = {
  status: Pick<typeof STATUSES> | null;
  eligibility: Pick<typeof ELIGIBILITIES> | null;
  origin: Pick<typeof ORIGINS> | null;
  country: string | null;
  category: Pick<typeof CATEGORIES> | null;
  minScore: number | null;
  q: string | null;
  sort: Pick<typeof SORTS>;
  page: number;
};

type Params = Record<string, string | string[] | undefined>;
const first = (v: string | string[] | undefined): string | undefined => (Array.isArray(v) ? v[0] : v);
const oneOf = <T extends readonly string[]>(list: T, v: string | undefined): T[number] | null => (v !== undefined && (list as readonly string[]).includes(v) ? (v as T[number]) : null);

export function parseFilters(p: Params): Filters {
  const country = first(p.country)?.trim().toUpperCase();
  const minScore = Number(first(p.min_score));
  const page = Number.parseInt(first(p.page) ?? "1", 10);
  const q = first(p.q)?.trim().slice(0, 100);
  return {
    status: oneOf(STATUSES, first(p.status)),
    eligibility: oneOf(ELIGIBILITIES, first(p.eligibility)),
    origin: oneOf(ORIGINS, first(p.origin)),
    country: country && /^[A-Z]{2}$/.test(country) ? country : null,
    category: oneOf(CATEGORIES, first(p.category)),
    minScore: first(p.min_score) !== undefined && first(p.min_score) !== "" && Number.isFinite(minScore) && minScore >= 0 && minScore <= 100 ? minScore : null,
    q: q ? q : null,
    sort: oneOf(SORTS, first(p.sort)) ?? "score",
    page: Number.isFinite(page) && page >= 1 && page <= 10_000 ? page : 1,
  };
}

const ORDER: Record<Pick<typeof SORTS>, string> = {
  score: "o.current_score DESC NULLS LAST, o.updated_at DESC",
  deadline: "o.deadline_at ASC NULLS LAST, o.current_score DESC NULLS LAST",
  updated: "o.updated_at DESC",
};

export const LIST_COLUMNS = `o.id, o.title_original, o.title_pt, o.issuer, o.country, o.city_or_region, o.category, o.status,
  o.eligibility_status, o.relocation_condition, o.data_origin, o.is_official, o.value_amount, o.value_currency, o.value_period,
  o.value_gross_net, o.deadline_at, o.deadline_tz, o.deadline_confirmed, o.audit_status, o.current_score, o.canonical_url,
  o.last_verified_at, o.updated_at, o.source_id,
  (SELECT count(*) FROM evidence_spans e WHERE e.opportunity_id = o.id AND e.version_id = o.current_version_id AND e.verified)::int AS evidence_verified,
  (SELECT count(*) FROM evidence_spans e WHERE e.opportunity_id = o.id AND e.version_id = o.current_version_id)::int AS evidence_total`;

export function buildOpportunityQuery(f: Filters, pageSize = DEFAULT_PAGE_SIZE): { text: string; values: unknown[] } {
  const values: unknown[] = [];
  const where: string[] = [];
  const add = (sql: string, v: unknown) => { values.push(v); where.push(sql.replace("$", `$${values.length}`)); };
  if (f.status) add("o.status = $", f.status);
  if (f.eligibility) add("o.eligibility_status = $", f.eligibility);
  if (f.origin) add("o.data_origin = $", f.origin);
  if (f.country) add("o.country = $", f.country);
  if (f.category) add("o.category = $", f.category);
  if (f.minScore !== null) add("o.current_score >= $", f.minScore);
  if (f.q) {
    const like = `%${f.q.replace(/[\\%_]/g, (c) => `\\${c}`)}%`;
    values.push(like);
    const n = values.length;
    where.push(`(o.title_original ILIKE $${n} OR o.title_pt ILIKE $${n} OR o.issuer ILIKE $${n} OR o.city_or_region ILIKE $${n})`);
  }
  values.push(pageSize + 1, (f.page - 1) * pageSize);   // +1 para saber se há próxima página
  const text = `SELECT ${LIST_COLUMNS} FROM opportunities o${where.length ? ` WHERE ${where.join(" AND ")}` : ""} ` +
    `ORDER BY ${ORDER[f.sort]} LIMIT $${values.length - 1} OFFSET $${values.length}`;
  return { text, values };
}
