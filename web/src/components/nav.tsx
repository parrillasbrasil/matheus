"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "/", label: "Visão geral" },
  { href: "/opportunities", label: "Oportunidades" },
  { href: "/applications", label: "Candidaturas" },
  { href: "/alerts", label: "Alertas" },
  { href: "/sources", label: "Fontes" },
  { href: "/jobs", label: "Coletas e falhas" },
  { href: "/costs", label: "Custos de IA" },
];

export function Nav() {
  const path = usePathname();
  return (
    <nav aria-label="Principal" className="flex gap-1 overflow-x-auto md:flex-col md:overflow-visible">
      {ITEMS.map((i) => {
        const active = i.href === "/" ? path === "/" : path.startsWith(i.href);
        return (
          <Link key={i.href} href={i.href} aria-current={active ? "page" : undefined}
            className={`whitespace-nowrap border-l-4 px-3 py-2 no-underline ${active ? "border-ink bg-sheet font-semibold text-ink" : "border-transparent text-ink-soft hover:bg-sheet hover:text-ink"}`}>
            {i.label}
          </Link>
        );
      })}
    </nav>
  );
}
