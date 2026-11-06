"use client";

import { usePathname } from "next/navigation";
import navigation from "../../../../static/navigation.json";
import HeaderActions from "./header-actions";

const pages = [
  { ...navigation.home, group: "Central DP" },
  ...navigation.groups.flatMap(group => group.items.map(item => ({ ...item, group: group.label }))),
].sort((a, b) => b.href.length - a.href.length);

export default function AppHeader() {
  const pathname = usePathname() || "/";
  const page = pages.find(item => pathname === item.href || pathname.startsWith(`${item.href}/`)) || pages.find(item => item.href === "/central-dp/inicio");
  const detail = pathname.startsWith("/central-dp/casos/") ? "Detalhe do caso" : pathname.startsWith("/central-dp/rescisoes/") ? "Detalhe da rescisão" : "";
  return <header className="topbar app-topbar">
    <div className="app-topbar-heading"><span className="eyebrow">{page.group}</span><h1>{detail || page.label}</h1></div>
    <div className="top-actions"><HeaderActions /></div>
  </header>;
}
