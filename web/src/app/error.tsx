"use client";

export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <main className="mx-auto max-w-md px-4 py-24">
      <h1 className="text-3xl font-semibold">Não foi possível carregar a página</h1>
      <p className="mt-2 text-ink-soft">O banco de dados pode estar indisponível. Os dados já coletados não são afetados. Tente de novo em instantes.</p>
      <button type="button" onClick={reset} className="btn mt-4">Tentar de novo</button>
    </main>
  );
}
