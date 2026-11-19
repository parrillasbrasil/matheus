// Autenticação do painel: um administrador definido por variáveis de ambiente.
// Sessão = cookie assinado com HMAC-SHA256 (Web Crypto, funciona no proxy e nos handlers).
// Senha = scrypt com sal. Nada de credencial no código ou no banco.
import { scrypt as scryptCb, randomBytes, timingSafeEqual } from "node:crypto";

export const SESSION_COOKIE = "radar_session";
export const SESSION_TTL_SECONDS = 12 * 60 * 60;
const SCRYPT = { N: 16384, r: 8, p: 1, keylen: 64 };

function scrypt(password: string, salt: Buffer, keylen: number, opts: { N: number; r: number; p: number }): Promise<Buffer> {
  return new Promise((resolve, reject) =>
    scryptCb(password, salt, keylen, { N: opts.N, r: opts.r, p: opts.p, maxmem: 64 * 1024 * 1024 }, (e, k) => (e ? reject(e) : resolve(k))));
}

export async function hashPassword(password: string): Promise<string> {
  if (typeof password !== "string" || password.length < 12) throw new Error("A senha precisa ter pelo menos 12 caracteres.");
  const salt = randomBytes(16);
  const key = await scrypt(password, salt, SCRYPT.keylen, SCRYPT);
  // Separador ":" (e não "$"): o Docker Compose interpreta "$" em arquivos .env e quebraria o hash em silêncio.
  return ["scrypt", SCRYPT.N, SCRYPT.r, SCRYPT.p, salt.toString("base64url"), key.toString("base64url")].join(":");
}

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  try {
    if (typeof stored !== "string" || typeof password !== "string") return false;
    const parts = stored.split(":");
    if (parts.length !== 6 || parts[0] !== "scrypt") return false;
    const [N, r, p] = [Number(parts[1]), Number(parts[2]), Number(parts[3])];
    if (![N, r, p].every((n) => Number.isInteger(n) && n > 0) || N > 1 << 20) return false;
    const salt = Buffer.from(parts[4], "base64url");
    const expected = Buffer.from(parts[5], "base64url");
    if (salt.length < 8 || expected.length < 16) return false;
    const actual = await scrypt(password, salt, expected.length, { N, r, p });
    return actual.length === expected.length && timingSafeEqual(actual, expected);
  } catch {
    return false;
  }
}

// ---- sessão -----------------------------------------------------------------------------------
const enc = new TextEncoder();
const b64u = (buf: ArrayBuffer | Uint8Array) => Buffer.from(buf instanceof Uint8Array ? buf : new Uint8Array(buf)).toString("base64url");

async function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

export async function signSession(user: string, secret: string, nowMs: number, ttlSeconds = SESSION_TTL_SECONDS): Promise<string> {
  const payload = b64u(enc.encode(JSON.stringify({ u: user, exp: nowMs + ttlSeconds * 1000 })));
  const sig = await crypto.subtle.sign("HMAC", await hmacKey(secret), enc.encode(payload));
  return `${payload}.${b64u(sig)}`;
}

export async function verifySession(token: string | undefined, secret: string, nowMs: number): Promise<{ user: string } | null> {
  try {
    if (typeof token !== "string") return null;
    const [payload, sig, extra] = token.split(".");
    if (!payload || !sig || extra !== undefined) return null;
    const ok = await crypto.subtle.verify("HMAC", await hmacKey(secret), Buffer.from(sig, "base64url"), enc.encode(payload));
    if (!ok) return null;
    const data = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
    if (typeof data.u !== "string" || typeof data.exp !== "number" || data.exp < nowMs) return null;
    return { user: data.u };
  } catch {
    return null;
  }
}

// ---- configuração -----------------------------------------------------------------------------
export type AuthConfig = { user: string; passwordHash: string; secret: string };

export function isAuthConfigured(env: Record<string, string | undefined>): boolean {
  return Boolean(env.ADMIN_USER && env.ADMIN_PASSWORD_HASH && env.SESSION_SECRET && env.SESSION_SECRET.length >= 32);
}

export function assertAuthConfig(env: Record<string, string | undefined>): AuthConfig {
  const missing = (n: string) => new Error(`Variável de ambiente ${n} ausente ou inválida (veja .env.example).`);
  if (!env.ADMIN_USER) throw missing("ADMIN_USER");
  if (!env.ADMIN_PASSWORD_HASH) throw missing("ADMIN_PASSWORD_HASH");
  if (!env.SESSION_SECRET || env.SESSION_SECRET.length < 32) throw new Error("SESSION_SECRET precisa ter pelo menos 32 caracteres (veja .env.example).");
  return { user: env.ADMIN_USER, passwordHash: env.ADMIN_PASSWORD_HASH, secret: env.SESSION_SECRET };
}

// ---- limite de tentativas (em memória: suficiente para uma instância única) -------------------
export class LoginLimiter {
  private hits = new Map<string, { n: number; first: number }>();
  constructor(private max = 5, private windowMs = 15 * 60_000) {}
  allowed(key: string, now: number): boolean {
    const h = this.hits.get(key);
    if (!h) return true;
    if (now - h.first > this.windowMs) { this.hits.delete(key); return true; }
    return h.n < this.max;
  }
  fail(key: string, now: number): void {
    const h = this.hits.get(key);
    if (!h || now - h.first > this.windowMs) this.hits.set(key, { n: 1, first: now });
    else h.n += 1;
  }
  reset(key: string): void { this.hits.delete(key); }
}

/** Só caminhos relativos do próprio site (evita open redirect). */
export function safeNext(next: string | null | undefined): string {
  if (typeof next !== "string" || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return "/";
  return next;
}
