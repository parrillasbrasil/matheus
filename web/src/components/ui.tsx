import { noticeText } from "@/lib/format";

export function Notice({ notice, error }: { notice?: string; error?: string }) {
  const n = noticeText(notice), e = noticeText(error);
  if (!n && !e) return null;
  return (
    <p role={e ? "alert" : "status"} className={`mb-5 border-l-4 px-4 py-2 ${e ? "border-inelig bg-inelig-bg text-inelig" : "border-eligible bg-eligible-bg text-eligible"}`}>{e ?? n}</p>
  );
}

export function PageTitle({ title, hint }: { title: string; hint?: string }) {
  return (
    <header className="mb-6">
      <h1 className="text-3xl font-semibold">{title}</h1>
      {hint && <p className="mt-1 max-w-[65ch] text-ink-soft">{hint}</p>}
    </header>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="border border-dashed border-rule bg-sheet px-4 py-6 text-ink-soft">{children}</p>;
}

/** Botão que envia um POST para a API (sem JavaScript no cliente). `next` leva de volta à página atual. */
export function PostButton({ action, fields = {}, children, quiet = false, next }: { action: string; fields?: Record<string, string>; children: React.ReactNode; quiet?: boolean; next: string }) {
  return (
    <form method="post" action={action} className="inline">
      {Object.entries({ ...fields, next }).map(([k, v]) => <input key={k} type="hidden" name={k} value={v} />)}
      <button type="submit" className={`btn ${quiet ? "btn-quiet" : ""}`}>{children}</button>
    </form>
  );
}

export const searchParamsToString = (p: Record<string, string | string[] | undefined>, drop: string[] = []) => {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) if (!drop.includes(k) && typeof v === "string" && v) u.set(k, v);
  return u.toString();
};
