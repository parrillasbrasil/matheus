import { describe, expect, it } from "vitest";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { verifyPassword } from "@/lib/auth";

const script = path.resolve(__dirname, "../scripts/hash-password.mjs");
const run = (input: string) => spawnSync("node", [script], { input, encoding: "utf8" });

describe("scripts/hash-password.mjs", () => {
  it("gera um hash que o painel aceita, lendo a senha da entrada padrão (fora do histórico do shell)", async () => {
    const r = run("uma-senha-bem-longa-123\n");
    expect(r.status).toBe(0);
    const hash = r.stdout.trim();
    expect(hash.startsWith("scrypt:")).toBe(true);
    expect(hash).not.toContain("$");   // seguro em .env do Docker Compose
    expect(await verifyPassword("uma-senha-bem-longa-123", hash)).toBe(true);
    expect(await verifyPassword("outra-senha-qualquer-1", hash)).toBe(false);
  });
  it("recusa senha curta com mensagem clara e sem imprimir hash", () => {
    const r = run("curta\n");
    expect(r.status).toBe(1);
    expect(r.stdout).toBe("");
    expect(r.stderr).toMatch(/12 caracteres/);
  });
});
