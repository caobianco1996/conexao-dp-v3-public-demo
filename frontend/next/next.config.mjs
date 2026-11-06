const pythonOrigin = process.env.CONEXAODP_PYTHON_ORIGIN || "http://127.0.0.1:8792";

/** @type {import('next').NextConfig} */
const nextConfig = {
  distDir: process.env.CONEXAODP_NEXT_DIST_DIR || ".next",
  experimental: { cpus: 1 },
  async rewrites() {
    return { beforeFiles: [
      { source: "/entrar", destination: `${pythonOrigin}/entrar` },
      { source: "/sair", destination: `${pythonOrigin}/sair` },
      { source: "/central-dp/processos/salvar", destination: `${pythonOrigin}/central-dp/processos` },
      { source: "/api/ui/:path*", destination: `${pythonOrigin}/api/ui/:path*` },
      { source: "/api/access", destination: `${pythonOrigin}/api/access` },
      { source: "/api/access/:path*", destination: `${pythonOrigin}/api/access/:path*` },
      { source: "/api/zpro/:path*", destination: `${pythonOrigin}/api/zpro/:path*` },
      { source: "/webhooks/clicksign/:path*", destination: `${pythonOrigin}/webhooks/clicksign/:path*` },
      { source: "/api/clicksign/:path*", destination: `${pythonOrigin}/api/clicksign/:path*` },
      { source: "/api/voxfy/:path*", destination: `${pythonOrigin}/api/voxfy/:path*` },
      { source: "/api/email/:path*", destination: `${pythonOrigin}/api/email/:path*` },
      { source: "/api/integrations/:path*", destination: `${pythonOrigin}/api/integrations/:path*` },
      { source: "/api/chat", destination: `${pythonOrigin}/api/chat` },
      { source: "/api/chat/:path*", destination: `${pythonOrigin}/api/chat/:path*` },
      // Python-rendered pages reference their assets and form actions from the origin root.
      { source: "/static/:path*", destination: `${pythonOrigin}/static/:path*` },
      { source: "/manual-assets/:path*", destination: `${pythonOrigin}/manual-assets/:path*` },
      { source: "/central-dp/casos/criar", destination: `${pythonOrigin}/central-dp/casos/criar` },
      { source: "/central-dp/casos/:id/editar", destination: `${pythonOrigin}/central-dp/casos/:id/editar` },
      { source: "/central-dp/casos/:id/itens", destination: `${pythonOrigin}/central-dp/casos/:id/itens` },
      { source: "/central-dp/casos/:id/status", destination: `${pythonOrigin}/central-dp/casos/:id/status` },
      { source: "/central-dp/casos/:id/check/:item", destination: `${pythonOrigin}/central-dp/casos/:id/check/:item` },
      { source: "/central-dp/casos/v2/:action", destination: `${pythonOrigin}/central-dp/casos/v2/:action` },
      // Planejamento de Lotes e Calculadoras DP são páginas Next; suas APIs usam /api/ui.
      { source: "/rotinas/tributos/:path*", destination: `${pythonOrigin}/rotinas/tributos/:path*` },
      { source: "/rotinas/tributos", destination: `${pythonOrigin}/rotinas/tributos` },
      { source: "/analytics/tabelas/exportar", destination: `${pythonOrigin}/analytics/tabelas/exportar` },
      { source: "/analytics/atualizar", has: [{ type: "query", key: "legacy", value: "1" }], destination: `${pythonOrigin}/analytics/atualizar` },
      { source: "/administracao/:path((?!contas(?:/|$)).*)", destination: `${pythonOrigin}/administracao/:path*` },
    ] };
  },
};

export default nextConfig;
