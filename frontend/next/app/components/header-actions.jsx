"use client";

import { useEffect, useState } from "react";
import QuickCalculator from "./quick-calculator";

const THEME_KEY = "conexao-dp-theme";

function CalculatorIcon() {
  return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="5" y="3" width="14" height="18" rx="2" />
    <path d="M8 7h8M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15h.01M8 18h.01M12 18h.01M16 18h.01" />
  </svg>;
}

function ThemeIcon({ dark }) {
  return dark
    ? <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" /></svg>
    : <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20.4 15.3A8.6 8.6 0 0 1 8.7 3.6 8.7 8.7 0 1 0 20.4 15.3Z" /></svg>;
}

export default function HeaderActions({ csrf = "" }) {
  const [dark, setDark] = useState(false);
  const [sessionCsrf, setSessionCsrf] = useState(csrf);

  useEffect(() => {
    const syncTheme = () => {
      let saved = "light";
      try { saved = window.localStorage.getItem(THEME_KEY) || window.localStorage.getItem("conexao-dp.theme") || "light"; } catch {}
      const theme = saved === "dark" ? "dark" : "light";
      document.body.dataset.theme = theme;
      setDark(theme === "dark");
    };
    syncTheme();
    window.addEventListener("storage", syncTheme);
    return () => window.removeEventListener("storage", syncTheme);
  }, []);

  useEffect(() => {
    let active = true;
    if (csrf) {
      setSessionCsrf(csrf);
      return () => { active = false; };
    }
    fetch("/api/chat", { credentials: "same-origin", cache: "no-store" })
      .then(response => response.ok ? response.json() : null)
      .then(data => { if (active && data?.csrf) setSessionCsrf(data.csrf); })
      .catch(() => {});
    return () => { active = false; };
  }, [csrf]);

  function toggleTheme() {
    const nextTheme = dark ? "light" : "dark";
    document.body.dataset.theme = nextTheme;
    try {
      window.localStorage.setItem(THEME_KEY, nextTheme);
      window.localStorage.setItem("conexao-dp.theme", nextTheme);
    } catch {}
    setDark(nextTheme === "dark");
  }

  return <>
    {sessionCsrf
      ? <form className="header-account-form" action="/sair" method="post"><input type="hidden" name="csrf" value={sessionCsrf} /><button className="btn btn-outline-secondary btn-sm header-account-action" type="submit">Trocar conta</button></form>
      : <a className="btn btn-outline-secondary btn-sm header-account-action" href="/entrar">Trocar conta</a>}
    <QuickCalculator><CalculatorIcon /></QuickCalculator>
    <button className="btn btn-outline-secondary btn-sm header-icon-action" type="button" onClick={toggleTheme} aria-label={dark ? "Ativar modo claro" : "Ativar modo escuro"} title={dark ? "Ativar modo claro" : "Ativar modo escuro"}><ThemeIcon dark={dark} /></button>
  </>;
}
