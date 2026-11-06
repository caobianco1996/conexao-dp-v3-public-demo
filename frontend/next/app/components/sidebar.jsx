"use client";

import { usePathname } from "next/navigation";
import { createElement, useEffect, useRef, useState } from "react";
import navigation from "../../../../static/navigation.json";
import { useChatUnread } from "./chat-unread";

const STORAGE_KEY = "dp.sidebar.groups.v3";
const normalize = value => String(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[-×·]/g, "").replace(/\s+/g, " ").trim();
const isCurrent = (pathname, href) => pathname === href || pathname.startsWith(href + "/") || (href === "/central-dp/inicio" && pathname === "/");

function Icon({ name, className = "" }) {
  return <svg className={"global-link-icon " + className} viewBox="0 0 24 24" width="18" height="18"
    fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {(navigation.icons[name] || navigation.icons.home).map((shape, index) => createElement(shape.tag, { ...shape.attrs, key: index }))}
  </svg>;
}

export default function Sidebar() {
  const pathname = usePathname() || "/";
  const chatUnread = useChatUnread();
  const [sibQuery, setSibQuery] = useState(undefined);
  const [accessPermissions, setAccessPermissions] = useState(null);
  const [search, setSearch] = useState("");
  const [openGroups, setOpenGroups] = useState({});
  const [hydrated, setHydrated] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const sidebarRef = useRef(null);
  const navRef = useRef(null);
  const triggerRef = useRef(null);
  const closeRef = useRef(null);
  const activeGroup = navigation.groups.find(group => group.items.some(item => isCurrent(pathname, item.href)))?.id;

  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/access/self', { credentials: 'same-origin', cache: 'no-store', signal: controller.signal })
      .then(response => response.ok ? response.json() : null)
      .then(payload => { if (payload?.permissions) setAccessPermissions(payload.permissions); })
      .catch(() => {});
    return () => controller.abort();
  }, [pathname]);

  const allowedPage = href => {
    if (accessPermissions === null) return true;
    const required = href === '/rotinas/setor' ? 'registry.manage' : href.startsWith('/administracao/') ? 'access.manage' : href.startsWith('/cadastros/') ? 'registry.view'
      : href.startsWith('/analytics') ? 'analytics.view' : href === '/central-dp/afastamentos' ? 'absences.view'
      : href === '/central-dp/prorrogacoes' ? 'extensions.view' : href === '/central-dp/rescisoes' ? 'rescissions.view'
      : ['/central-dp/assinaturas', '/central-dp/integracoes', '/central-dp/email', '/central-dp/voxfy'].includes(href) ? 'integrations.manage' : '';
    return !required || accessPermissions.includes(required);
  };

  useEffect(() => {
    let saved = {};
    try { saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "{}"); } catch {}
    const valid = Object.fromEntries(navigation.groups.filter(group => typeof saved?.[group.id] === "boolean").map(group => [group.id, saved[group.id]]));
    setOpenGroups({ ...valid, ...(activeGroup ? { [activeGroup]: true } : {}) });
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (activeGroup) setOpenGroups(current => ({ ...current, [activeGroup]: true }));
    setMobileOpen(false);
  }, [pathname, activeGroup]);

  useEffect(() => {
    if (!hydrated) return;
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(openGroups)); } catch {}
  }, [openGroups, hydrated]);

  useEffect(() => {
    if (!hydrated || !navRef.current) return;
    try { navRef.current.scrollTop = Number(sessionStorage.getItem("dp.sidebar.scroll") || 0); } catch {}
    const frame = requestAnimationFrame(() => {
      const nav = navRef.current;
      const active = nav?.querySelector('[aria-current="page"]');
      if (!active) return;
      const bounds = nav.getBoundingClientRect(), link = active.getBoundingClientRect();
      if (link.top < bounds.top) nav.scrollTop -= bounds.top - link.top;
      else if (link.bottom > bounds.bottom) nav.scrollTop += link.bottom - bounds.bottom;
    });
    return () => cancelAnimationFrame(frame);
  }, [hydrated]);

  useEffect(() => {
    if (!mobileOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const onKey = event => {
      if (event.key === "Escape") { event.preventDefault(); setMobileOpen(false); return; }
      if (event.key !== "Tab") return;
      const controls = [...sidebarRef.current.querySelectorAll("a,button,summary,input")].filter(element => !element.disabled && element.getClientRects().length);
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    const onResize = () => { if (window.innerWidth > 800) setMobileOpen(false); };
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
      triggerRef.current?.focus();
    };
  }, [mobileOpen]);

  useEffect(() => {
    let active = true, lock = false;
    const update = async () => {
      if (lock || document.hidden) return;
      lock = true;
      try {
        let response = await fetch("/api/integrations/settings", { credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(8000) });
        let data = response.ok ? await response.json() : null;
        if (!data) {
          response = await fetch("/api/ui/snapshot", { credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(8000) });
          if (!response.ok) throw new Error("snapshot unavailable");
          data = await response.json();
        }
        if (active) setSibQuery({ available: true, retrying: Boolean(data?.sibScan?.retrying),
          active: Boolean(data?.sibAutoRefresh?.running || data?.sibScan?.autoResume || data?.sibScan?.running || data?.sibScan?.inFlight || data?.sibRefresh?.running || data?.sibRefresh?.inFlight || data?.sibIssueJob?.running || data?.sibIssueJob?.inFlight),
          started: Boolean(data?.sibScan?.started), done: Boolean(data?.sibScan?.done) });
      } catch { if (active) setSibQuery({ available: false, active: false }); } finally { lock = false; }
    };
    update();
    const id = setInterval(update, 5000);
    return () => { active = false; clearInterval(id); };
  }, []);

  const query = normalize(search);
  const terms = query.split(" ").filter(Boolean);
  const matches = value => terms.every(term => normalize(value).includes(term));
  const visibleGroups = navigation.groups.map(group => ({ ...group, items: group.items.filter(item => allowedPage(item.href) && matches(group.label + " " + item.label)) })).filter(group => group.items.length);
  const sibQueryActive = Boolean(sibQuery?.active);
  const sibQueryLabel = sibQuery === undefined ? "Verificando SIB…" : !sibQuery.available ? "Status indisponível" : sibQuery.retrying ? "Reconectando SIB" : sibQueryActive ? "Consultando SIB" : sibQuery.done ? "Faixa consultada" : sibQuery.started ? "Consulta pausada" : "Pronto para consultar";
  const renderLink = item => {
    const active = isCurrent(pathname, item.href);
    return <a key={item.href} href={item.href} data-nav-label={item.label} className={active ? "active" : ""} aria-current={active ? "page" : undefined} onClick={() => setMobileOpen(false)}>
      <Icon name={item.icon} /><span>{item.label}</span>
      {item.href === "/central-dp/chat" && chatUnread > 0 && <b className="sidebar-chat-badge" aria-label={chatUnread + " mensagens não lidas"}>{chatUnread > 99 ? "99+" : chatUnread}</b>}
    </a>;
  };

  return <>
    <button ref={triggerRef} type="button" className="global-menu-trigger next-sidebar-trigger" aria-controls="global-navigation" aria-expanded={mobileOpen} onClick={() => setMobileOpen(true)}>
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M3 6h18M3 12h18M3 18h18" /></svg><span>Menu</span>
    </button>
    <button type="button" className="global-menu-backdrop" hidden={!mobileOpen} aria-label="Fechar menu de navegação" onClick={() => setMobileOpen(false)} />
    <aside ref={sidebarRef} id="global-navigation" className={"global-sidebar" + (mobileOpen ? " is-open" : "")} aria-label="Navegação principal">
      <div className="global-brand"><a href="/central-dp/inicio" aria-label="Conexão DP — início"><img src="/static/logo.svg" alt="Conexão Empregos" /></a>
        <button ref={closeRef} className="global-mobile-close" type="button" aria-label="Fechar menu" onClick={() => setMobileOpen(false)}>×</button>
      </div>
      <div className={"global-snapshot " + (sibQueryActive ? "is-active" : "is-idle")} aria-live="polite"><i aria-hidden="true" /><div><span>Consulta de cadastros SIB</span><strong>{sibQueryLabel}</strong></div></div>
      <nav className="global-nav-scroll" ref={navRef} onScroll={event => { try { sessionStorage.setItem("dp.sidebar.scroll", String(event.currentTarget.scrollTop)); } catch {} }}>
        <label className="global-nav-search"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><circle cx="10" cy="10" r="6" /><path d="m15 15 5 5" /></svg>
          <span className="visually-hidden">Buscar página</span><input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Buscar página…" autoComplete="off" disabled={!hydrated} />
        </label>
        {matches(navigation.home.label) && renderLink(navigation.home)}
        {visibleGroups.map(group => <details className="global-nav-group" key={group.id} data-nav-group={group.id} data-active={group.id === activeGroup}
          open={hydrated && (query ? true : (openGroups[group.id] ?? group.id === activeGroup))}>
          <summary onClick={event => { event.preventDefault(); if (!query) setOpenGroups(current => ({ ...current, [group.id]: !(current[group.id] ?? group.id === activeGroup) })); }}><Icon name={group.icon} /><span>{group.label}</span><svg className="global-group-chevron" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg></summary>
          <div className="global-nav-links">{group.items.map(renderLink)}</div>
        </details>)}
        {!visibleGroups.length && !matches(navigation.home.label) && <p className="global-nav-empty" role="status">Nenhuma página encontrada.</p>}
      </nav>
      <footer><div><strong>Base local compartilhada</strong><span>SQLite · Conexão DP V3</span></div></footer>
    </aside>
  </>;
}
