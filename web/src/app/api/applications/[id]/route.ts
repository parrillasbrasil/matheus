import { APPLICATION_STATUSES, transitionApplication, type ApplicationStatus } from "@/lib/actions";
import { getPool } from "@/lib/db";
import { guardPost, outcome, STATUS_BY_REASON } from "@/lib/http";

// Quem aprovou/enviou é SEMPRE o usuário da sessão; qualquer "approved_by" no corpo é ignorado.
export async function POST(req: Request, ctx: { params: Promise<{ id: string }> }): Promise<Response> {
  const g = await guardPost(req);
  if (g instanceof Response) return g;
  const { id } = await ctx.params;
  const to = g.body.status as ApplicationStatus;
  if (!APPLICATION_STATUSES.includes(to)) return outcome(req, g.body, 400, { error: "invalid_status" }, "invalid_status");
  const r = await transitionApplication(getPool(), id, to, g.user, new Date());
  return r.ok ? outcome(req, g.body, 200, { ok: true }, `application_${to}`)
              : outcome(req, g.body, STATUS_BY_REASON[r.reason] ?? 409, { error: r.reason }, r.reason);
}
