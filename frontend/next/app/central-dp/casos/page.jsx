import CasesPage from "../../components/cases-page";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }) {
  const params = await searchParams;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (Array.isArray(value)) value.forEach(item => query.append(key, item));
    else if (value != null) query.set(key, value);
  }
  return <CasesPage queryString={query.toString()} />;
}
