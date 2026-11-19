import { describe, expect, it } from "vitest";
import { formatMoney, formatDeadline, eligibilityLabel, originLabel, statusLabel, benefitLabel, noticeText } from "@/lib/format";

describe("formatMoney", () => {
  it("mostra valor, moeda, período e bruto/líquido; nada de inventar", () => {
    expect(formatMoney({ amount: "5000", currency: "EUR", period: "one_time", gross_net: "net" })).toBe("EUR 5.000 · pago uma vez · líquido");
    expect(formatMoney({ amount: "1200.5", currency: "USD", period: "monthly", gross_net: "gross" })).toBe("USD 1.200,50 · por mês · bruto");
    expect(formatMoney({ amount: "17.5", currency: "USD", period: "hourly", gross_net: "unknown" })).toBe("USD 17,50 · por hora · bruto/líquido não informado");
  });
  it("valor ou moeda ausentes = 'desconhecido' (nunca zero)", () => {
    expect(formatMoney({ amount: null, currency: "EUR", period: "one_time", gross_net: "net" })).toBe("desconhecido");
    expect(formatMoney({ amount: "100", currency: null, period: null, gross_net: null })).toBe("desconhecido");
  });
});

describe("formatDeadline", () => {
  it("prazo confirmado com fuso informado mostra o fuso", () => {
    const r = formatDeadline({ deadline_at: "2026-12-01T22:59:00Z", deadline_tz: "Europe/Rome", deadline_confirmed: true, deadline_tz_assumed: false });
    expect(r.confirmed).toBe(true);
    expect(r.text).toBe("01/12/2026 23:59 (Europe/Rome)");
  });
  it("sem fuso na fonte: mostra em UTC e avisa que o fuso não foi informado", () => {
    const r = formatDeadline({ deadline_at: "2026-12-01T00:00:00Z", deadline_tz: null, deadline_confirmed: false, deadline_tz_assumed: false });
    expect(r.confirmed).toBe(false);
    expect(r.text).toBe("01/12/2026 00:00 UTC · fuso não informado pela fonte");
  });
  it("fuso assumido pelo radar é sinalizado", () => {
    expect(formatDeadline({ deadline_at: "2026-12-01T00:00:00Z", deadline_tz: "Europe/Rome", deadline_confirmed: false, deadline_tz_assumed: true }).text).toContain("fuso assumido");
  });
  it("sem prazo = 'desconhecido'", () => {
    expect(formatDeadline({ deadline_at: null, deadline_tz: null, deadline_confirmed: false, deadline_tz_assumed: false })).toEqual({ text: "desconhecido", confirmed: false });
  });
});

describe("rótulos", () => {
  it("traduz códigos e devolve o próprio código quando não conhece (nada some em silêncio)", () => {
    expect(eligibilityLabel("needs_confirmation")).toBe("Precisa confirmar");
    expect(originLabel("fixture")).toBe("Teste (fixture)");
    expect(originLabel("unknown")).toBe("Origem desconhecida");
    expect(statusLabel("open")).toBe("Aberta");
    expect(benefitLabel("relocation_grant")).toBe("Auxílio de mudança");
    expect(eligibilityLabel("novo_valor")).toBe("novo_valor");
  });
  it("noticeText só traduz códigos conhecidos (texto vindo da URL nunca é exibido cru)", () => {
    expect(noticeText("scan_queued")).toMatch(/fila/i);
    expect(noticeText("<script>alert(1)</script>")).toBeNull();
  });
});
