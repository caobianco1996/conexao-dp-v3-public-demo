import RegistryPage from "../../components/registry-page";

export const dynamic = "force-dynamic";

const allowedViews = new Set(["pessoas", "vinculos", "empresas", "conflitos"]);

export default async function Page({ params, searchParams }) {
  const { view } = await params;
  if (!allowedViews.has(view)) {
    return <section className="page-content"><h1>Cadastro não encontrado</h1><a href="/central-dp/inicio">Voltar ao início</a></section>;
  }
  const query = await searchParams;
  return <RegistryPage view={view} initialQuery={query || {}} />;
}
