import { requeueDeadJob } from "@/lib/actions";
import { getPool } from "@/lib/db";
import { guardPost, outcome, STATUS_BY_REASON } from "@/lib/http";

export async function POST(req: Request, ctx: { params: Promise<{ id: string }> }): Promise<Response> {
  const g = await guardPost(req);
  if (g instanceof Response) return g;
  const { id } = await ctx.params;
  if (!/^\d{1,15}$/.test(id)) return outcome(req, g.body, 400, { error: "invalid_job_id" }, "invalid_job_id");
  const r = await requeueDeadJob(getPool(), Number(id), g.user, new Date());
  return r.ok ? outcome(req, g.body, 200, { ok: true }, "job_requeued")
              : outcome(req, g.body, STATUS_BY_REASON[r.reason] ?? 409, { error: r.reason }, r.reason);
}
