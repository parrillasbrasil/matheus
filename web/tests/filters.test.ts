import { describe, expect, it } from "vitest";
import { parseFilters, buildOpportunityQuery, DEFAULT_PAGE_SIZE } from "@/lib/filters";

describe("parseFilters", () => {
  it("usa padrões seguros quando nada é informado (todas as origens visíveis)", () => {
    const f = parseFilters({});
    expect(f).toMatchObject({ status: null, eligibility: null, origin: null, country: null, category: null, minScore: null, q: null, sort: "score", page: 1 });
  });
  it("aceita valores válidos", () => {
    const f = parseFilters({ status: "open", eligibility: "needs_confirmation", origin: "fixture", country: "it", category: "gov_relocation_incentive", min_score: "40", q: " Sardegna ", sort: "deadline", page: "3" });
    expect(f).toMatchObject({ status: "open", eligibility: "needs_confirmation", origin: "fixture", country: "IT", category: "gov_relocation_incentive", minScore: 40, q: "Sardegna", sort: "deadline", page: 3 });
  });
  it("ignora valores fora da lista permitida em vez de usá-los", () => {
    const f = parseFilters({ status: "open; DROP TABLE x", eligibility: "maybe", origin: "???", country: "ITALY", category: "Robert'); --", min_score: "abc", sort: "id; --", page: "-4" });
    expect(f).toMatchObject({ status: null, eligibility: null, origin: null, country: null, category: null, minScore: null, sort: "score", page: 1 });
  });
  it("com parâmetro repetido usa o primeiro valor", () => {
    expect(parseFilters({ status: ["open", "closed"] }).status).toBe("open");
  });
});

describe("buildOpportunityQuery", () => {
  it("sem filtros: sem WHERE, ordenado por score, paginado", () => {
    const { text, values } = buildOpportunityQuery(parseFilters({}));
    expect(text).toMatch(/FROM opportunities o ORDER BY/);   // sem WHERE na consulta principal
    expect(text).toMatch(/ORDER BY o\.current_score DESC NULLS LAST/);
    expect(values.slice(-2)).toEqual([DEFAULT_PAGE_SIZE + 1, 0]);
  });
  it("cada filtro vira condição parametrizada", () => {
    const { text, values } = buildOpportunityQuery(parseFilters({ status: "open", eligibility: "eligible", origin: "real", country: "IT", category: "job_with_package", min_score: "50", q: "bonus" }));
    for (const col of ["o.status = $", "o.eligibility_status = $", "o.data_origin = $", "o.country = $", "o.category = $", "o.current_score >= $"]) expect(text).toContain(col);
    expect(values).toEqual(expect.arrayContaining(["open", "eligible", "real", "IT", "job_with_package", 50, "%bonus%"]));
  });
  it("texto do usuário nunca entra no SQL, só em values; curingas são escapados", () => {
    const evil = "x'; DROP TABLE opportunities; --";
    const { text, values } = buildOpportunityQuery(parseFilters({ q: evil }));
    expect(text).not.toContain("DROP");
    expect(values).toContain(`%${evil}%`);
    const { values: v2 } = buildOpportunityQuery(parseFilters({ q: "100%_ok" }));
    expect(v2).toContain("%100\\%\\_ok%");
  });
  it("página 3 usa OFFSET correto", () => {
    const { values } = buildOpportunityQuery(parseFilters({ page: "3" }));
    expect(values.slice(-2)).toEqual([DEFAULT_PAGE_SIZE + 1, DEFAULT_PAGE_SIZE * 2]);
  });
  it("ordenação por prazo coloca prazos ausentes por último", () => {
    expect(buildOpportunityQuery(parseFilters({ sort: "deadline" })).text).toMatch(/ORDER BY o\.deadline_at ASC NULLS LAST/);
  });
});
