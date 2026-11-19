import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Global Opportunity Radar", template: "%s · Radar" },
  description: "Painel de monitoramento de oportunidades oficiais na Europa e nos EUA com verificação literal de evidências e elegibilidade determinística.",
  openGraph: {
    title: "Global Opportunity Radar",
    description: "Painel de monitoramento de oportunidades oficiais na Europa e nos EUA com verificação literal de evidências e elegibilidade determinística.",
  },
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
