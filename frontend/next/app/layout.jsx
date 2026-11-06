import "bootstrap/dist/css/bootstrap.min.css";
import "./styles.css";
import "./chat.css";
import "./manuals.css";
import "./calendar.css";
import "./calendar-bank.css";
import "./calendar-print.css";
import "./processes.css";
import "./sector.css";
import "./lot-planning.css";
import "./calculators.css";
import "./calculators-shortcuts.css";
import "./cases.css";
import "./clicksign.css";
import "./clicksign-extra.css";
import "./extensions.css";
import "./rescissions.css";
import "./registry.css";
import "./absences.css";
import "./people-analytics.css";
import "./people-reports.css";
import "./people-daily-email.css";
import "./voxfy.css";
import "./email-test.css";
import "./integrations.css";
import "./operations.css";
import "./product-refinement.css";
import Sidebar from "./components/sidebar";
import FloatingChat from "./components/floating-chat";
import AppHeader from "./components/app-header";

export const metadata = { title: "Central DP · Conexão Empregos", description: "Central DP local conectada ao SQLite da instalação Python." };

export default function RootLayout({ children }) {
  return <html lang="pt-BR"><head><link rel="stylesheet" href="/static/global-shell.css?v=functional-sidebar-v3" /></head><body>
    <div className="global-shell next-app-shell">
      <Sidebar />
      <main className="global-content app-main"><AppHeader />{children}</main>
      <FloatingChat />
    </div>
  </body></html>;
}
