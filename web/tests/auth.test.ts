import { describe, expect, it } from "vitest";
import {
  hashPassword, verifyPassword, signSession, verifySession, LoginLimiter, safeNext, assertAuthConfig,
} from "@/lib/auth";

const SECRET = "s".repeat(40);
const NOW = 1_800_000_000_000;

describe("senha (scrypt)", () => {
  it("aceita a senha correta e rejeita a errada", async () => {
    const h = await hashPassword("correta-horse-battery");
    expect(await verifyPassword("correta-horse-battery", h)).toBe(true);
    expect(await verifyPassword("errada", h)).toBe(false);
  });
  it("usa sal: o mesmo texto gera hashes diferentes", async () => {
    expect(await hashPassword("x12345678901")).not.toBe(await hashPassword("x12345678901"));
  });
  it("hash malformado ou vazio nunca autentica e não lança erro", async () => {
    for (const bad of ["", "abc", "scrypt:1:2:3", "scrypt:16384:8:1:zz:zz", "scrypt$16384$8$1$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", undefined as unknown as string]) {
      expect(await verifyPassword("qualquer", bad)).toBe(false);
    }
  });
  it("recusa senha curta ao gerar hash", async () => {
    await expect(hashPassword("curta")).rejects.toThrow(/12/);
  });
});

describe("sessão assinada (HMAC)", () => {
  it("ida e volta preserva o usuário", async () => {
    const t = await signSession("giuliano", SECRET, NOW, 3600);
    expect(await verifySession(t, SECRET, NOW + 1000)).toEqual({ user: "giuliano" });
  });
  it("expira", async () => {
    const t = await signSession("a", SECRET, NOW, 3600);
    expect(await verifySession(t, SECRET, NOW + 3600 * 1000 + 1)).toBeNull();
  });
  it("rejeita payload adulterado, segredo errado e lixo", async () => {
    const t = await signSession("a", SECRET, NOW, 3600);
    const [p, s] = t.split(".");
    const forged = Buffer.from(JSON.stringify({ u: "admin", exp: NOW + 9e9 })).toString("base64url");
    expect(await verifySession(`${forged}.${s}`, SECRET, NOW)).toBeNull();
    expect(await verifySession(t, "outro-segredo-qualquer-com-mais-de-32-chars", NOW)).toBeNull();
    for (const bad of ["", "x", "a.b", `${p}.`, `.${s}`, undefined as unknown as string]) {
      expect(await verifySession(bad, SECRET, NOW)).toBeNull();
    }
  });
});

describe("configuração de autenticação", () => {
  it("exige usuário, hash e segredo com 32+ caracteres", () => {
    expect(() => assertAuthConfig({})).toThrow(/ADMIN_USER/);
    expect(() => assertAuthConfig({ ADMIN_USER: "a", ADMIN_PASSWORD_HASH: "h", SESSION_SECRET: "curto" })).toThrow(/SESSION_SECRET/);
    expect(assertAuthConfig({ ADMIN_USER: "a", ADMIN_PASSWORD_HASH: "h", SESSION_SECRET: SECRET }).user).toBe("a");
  });
});

describe("limite de tentativas de login", () => {
  it("bloqueia a 6ª tentativa falha do mesmo IP dentro da janela e libera depois", () => {
    const l = new LoginLimiter(5, 15 * 60_000);
    for (let i = 0; i < 5; i++) { expect(l.allowed("1.1.1.1", NOW)).toBe(true); l.fail("1.1.1.1", NOW); }
    expect(l.allowed("1.1.1.1", NOW + 1000)).toBe(false);
    expect(l.allowed("2.2.2.2", NOW + 1000)).toBe(true);
    expect(l.allowed("1.1.1.1", NOW + 15 * 60_000 + 1)).toBe(true);
  });
  it("login bem-sucedido zera o contador", () => {
    const l = new LoginLimiter(2, 60_000);
    l.fail("a", NOW); l.reset("a");
    l.fail("a", NOW);
    expect(l.allowed("a", NOW)).toBe(true);
  });
});

describe("redirecionamento seguro", () => {
  it("só aceita caminhos relativos do próprio site", () => {
    expect(safeNext("/opportunities?x=1")).toBe("/opportunities?x=1");
    for (const bad of ["//evil.com", "https://evil.com", "/\\evil.com", "javascript:alert(1)", "", null, undefined]) {
      expect(safeNext(bad as string)).toBe("/");
    }
  });
});
