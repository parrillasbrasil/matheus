import { getPool } from "@/lib/db";
import { requireUser } from "@/lib/http";
import { getOpportunity } from "@/lib/queries";

export async function GET(req: Request, ctx: { params: Promise<{ id: string }> }): Promise<Response> {
  const u = await requireUser(req);
  if (u instanceof Response) return u;
  const { id } = await ctx.params;
  const found = await getOpportunity(getPool(), id);
  if (!found) return Response.json({ error: "not_found" }, { status: 404 });
  const { opportunity, ...rest } = found;
  return Response.json({ opportunity, ...rest });
}
