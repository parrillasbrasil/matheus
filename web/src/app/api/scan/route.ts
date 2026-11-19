import { enqueueScan } from "@/lib/actions";
import { getPool } from "@/lib/db";
import { guardPost, outcome, STATUS_BY_REASON } from "@/lib/http";

// Pede uma execução manual: só enfileira. Quem coleta é o worker, respeitando ToS, robots.txt e limites da fonte.
export async function POST(req: Request): Promise<Response> {
  const g = await guardPost(req);
  if (g instanceof Response) return g;
  const sourceId = g.body.source_id;
  if (!sourceId) return outcome(req, g.body, 400, { error: "source_id_required" }, "source_id_required");
  const r = await enqueueScan(getPool(), sourceId, g.user, new Date());
  return r.ok ? outcome(req, g.body, 202, { ok: true, job_id: r.jobId }, "scan_queued")
              : outcome(req, g.body, STATUS_BY_REASON[r.reason] ?? 409, { error: r.reason }, r.reason);
}
