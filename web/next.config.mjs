/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",          // imagem Docker mínima (Coolify)
  poweredByHeader: false,
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "same-origin" },
        { key: "Cache-Control", value: "no-store" },   // painel privado: nada de cache compartilhado
      ],
    }];
  },
};
export default nextConfig;
