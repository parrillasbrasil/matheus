import { getPool } from "@/lib/db";
import { requireUser } from "@/lib/http";
import { listSources } from "@/lib/queries";

export async function GET(req: Request): Promise<Response> {
  const u = await requireUser(req);
  if (u instanceof Response) return u;
  return Response.json({ items: await listSources(getPool()) });
}
