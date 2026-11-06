import RescissionsPage from "../../../components/rescissions-page";

export const dynamic = "force-dynamic";

export default async function Page({ params }) {
  const { id } = await params;
  return <RescissionsPage caseId={id} />;
}
