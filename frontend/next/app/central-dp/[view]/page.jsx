import CentralPage from "../../components/central-page";
import MessagesPage from "../../components/messages-page";
import ChatPage from "../../components/chat-page";
import ManualsPage from "../../components/manuals-page";
import ClicksignPage from "../../components/clicksign-page";
import ExtensionsPage from "../../components/extensions-page";
import RescissionsPage from "../../components/rescissions-page";
import VoxFyPage from "../../components/voxfy-page";
import EmailTestPage from "../../components/email-test-page";
import IntegrationsPage from "../../components/integrations-page";
import AbsencesPage from "../../components/absences-page";

export const dynamic = "force-dynamic";
const allowed = new Set(["inicio", "processos", "mensagens", "chat", "manuais", "assinaturas", "prorrogacoes", "afastamentos", "rescisoes", "voxfy", "email", "integracoes"]);
export default async function Page({ params, searchParams }) {
  const { view } = await params;
  if (!allowed.has(view)) return <section className="container-fluid p-4"><h1>Esta aba ainda não está disponível nesta versão.</h1><a href="/central-dp/inicio">Voltar ao início</a></section>;
  const query = await searchParams;
  if (view === "mensagens") return <MessagesPage />;
  if (view === "chat") return <ChatPage />;
  if (view === "manuais") return <ManualsPage />;
  if (view === "assinaturas") return <ClicksignPage />;
  if (view === "prorrogacoes") return <ExtensionsPage />;
  if (view === "rescisoes") return <RescissionsPage />;
  if (view === "voxfy") return <VoxFyPage />;
  if (view === "email") return <EmailTestPage />;
  if (view === "integracoes") return <IntegrationsPage />;
  if (view === "afastamentos") return <AbsencesPage />;
  return <CentralPage view={view} selectedId={query?.id || query?.local || ""} />;
}
