import PeopleAnalytics from "../../components/people-analytics";
import { notFound, redirect } from "next/navigation";

export const dynamic = "force-dynamic";
export default async function Page({ params, searchParams }) {
  const { section } = await params;
  if (!["avancadas", "painel", "como-usar"].includes(section)) notFound();
  if (section !== "como-usar") {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(await searchParams || {})) {
      for (const item of Array.isArray(value) ? value : [value]) query.append(key, item);
    }
    redirect(`/analytics/empresas${query.size ? `?${query}` : ""}`);
  }
  return <PeopleAnalytics section={`pro-${section}`} initialQuery={await searchParams || {}} />;
}
