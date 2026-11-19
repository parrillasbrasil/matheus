#!/usr/bin/env node
// Gera o valor de ADMIN_PASSWORD_HASH. A senha vem da entrada padrão (não fica no histórico do shell):
//   printf '%s' 'sua-senha-longa' | npm run --silent hash-password
// ou, interativo: npm run --silent hash-password   (digite a senha e Enter)
import { scryptSync, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";

const password = readFileSync(0, "utf8").replace(/\r?\n$/, "");
if (password.length < 12) {
  console.error("A senha precisa ter pelo menos 12 caracteres.");
  process.exit(1);
}
const N = 16384, r = 8, p = 1, keylen = 64;
const salt = randomBytes(16);
const key = scryptSync(password, salt, keylen, { N, r, p, maxmem: 64 * 1024 * 1024 });
console.log(["scrypt", N, r, p, salt.toString("base64url"), key.toString("base64url")].join(":"));
