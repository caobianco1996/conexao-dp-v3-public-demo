import PeopleAnalytics from "../../components/people-analytics";
import { notFound, redirect } from "next/navigation";

export const dynamic = "force-dynamic";
const sections = new Set(["relatorios", "visao-geral", "admitidos-demitidos", "admissoes", "desligamentos", "retencao", "tipos-contrato", "empresas", "contratos", "perfil-ativos", "qualidade", "atualizar", "tabelas"]);

export default async function Page({ params, searchParams }) {
  const { section } = await params;
  if (!sections.has(section)) notFound();
  const query = await searchParams || {};
  if (section === "atualizar") redirect("/central-dp/integracoes#sib-scan");
  const aliases = { admissoes: ["admitidos-demitidos", "admitted"], desligamentos: ["admitidos-demitidos", "terminated"], contratos: ["tipos-contrato", "active"] };
  if (aliases[section]) {
    const [destination, focus] = aliases[section];
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries({ ...query, focus: query.focus || focus })) {
      for (const item of Array.isArray(value) ? value : [value]) params.append(key, item);
    }
    redirect(`/analytics/${destination}?${params}`);
  }
  return <PeopleAnalytics key={section} section={section} initialQuery={query} />;
}
