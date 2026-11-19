import { getPool } from "@/lib/db";
import { workerHealth } from "@/lib/queries";

// Público de propósito (healthcheck do Docker/Coolify): só diz se o banco responde e se o worker dá sinal de vida.
export async function GET(): Promise<Response> {
  try {
    const pool = getPool();
    await pool.query("SELECT 1");
    const w = await workerHealth(pool, new Date());
    return Response.json({ status: w.state === "ok" ? "ok" : "degraded", db: true, worker: w.state });
  } catch {
    return Response.json({ status: "down", db: false, worker: "unknown" }, { status: 503 });
  }
}
