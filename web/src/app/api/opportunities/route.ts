import { getPool } from "@/lib/db";
import { parseFilters } from "@/lib/filters";
import { requireUser } from "@/lib/http";
import { listOpportunities } from "@/lib/queries";

export async function GET(req: Request): Promise<Response> {
  const u = await requireUser(req);
  if (u instanceof Response) return u;
  const params: Record<string, string | string[]> = {};
  for (const [k, v] of new URL(req.url).searchParams) params[k] = [...(params[k] ? [params[k]].flat() : []), v];
  const { rows, hasNext } = await listOpportunities(getPool(), parseFilters(params));
  return Response.json({ items: rows, has_next: hasNext });
}
