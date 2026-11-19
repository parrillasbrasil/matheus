import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto max-w-md px-4 py-24">
      <h1 className="text-3xl font-semibold">Página não encontrada</h1>
      <p className="mt-2 text-ink-soft">Esse endereço não existe ou a oportunidade foi removida.</p>
      <p className="mt-4"><Link href="/">Voltar à visão geral</Link></p>
    </main>
  );
}
