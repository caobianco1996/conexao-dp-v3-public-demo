import PeopleDailyEmail from "../../components/people-daily-email";

export const dynamic = "force-dynamic";
export const metadata = { title: "Envios automáticos · Conexão Empregos" };

export default function Page() {
  return <div className="page-content pa-page">
    <header className="pa-heading page-intro-toolbar">
      <div>
        <p>Organize os destinatários, as listas de pessoas e a frequência dos envios por empresa.</p>
      </div>
      <div className="pa-heading-actions">
        <a className="btn btn-outline-primary" href="/analytics/relatorios">Relatórios Excel</a>
        <a className="btn btn-outline-primary" href="/central-dp/email">Configurar e-mail</a>
      </div>
    </header>
    <PeopleDailyEmail />
  </div>;
}
