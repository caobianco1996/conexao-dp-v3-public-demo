import CasesPage from "../../../components/cases-page";

export const dynamic = "force-dynamic";

export default async function Page({ params, searchParams }) {
  const { id } = await params;
  const search = await searchParams;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(search || {})) {
    if (Array.isArray(value)) value.forEach(item => query.append(key, item));
    else if (value != null) query.set(key, value);
  }
  return <CasesPage caseId={id} queryString={query.toString()} />;
}
