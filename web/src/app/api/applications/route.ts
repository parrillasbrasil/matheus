import { createApplicationDraft } from "@/lib/actions";
import { getPool } from "@/lib/db";
import { guardPost, outcome, STATUS_BY_REASON } from "@/lib/http";

// Cria apenas um RASCUNHO. O radar nunca envia candidaturas.
export async function POST(req: Request): Promise<Response> {
  const g = await guardPost(req);
  if (g instanceof Response) return g;
  if (!g.body.opportunity_id) return outcome(req, g.body, 400, { error: "opportunity_id_required" }, "opportunity_id_required");
  const r = await createApplicationDraft(getPool(), g.body.opportunity_id, g.user, new Date());
  return r.ok ? outcome(req, g.body, 201, { ok: true, id: r.id }, "application_created")
              : outcome(req, g.body, STATUS_BY_REASON[r.reason] ?? 409, { error: r.reason }, r.reason);
}
