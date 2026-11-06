"use client";

import { useEffect, useRef, useState } from "react";
import { prepareCasesDocument } from "./cases-presentation";

export default function CasesPage({ caseId = "", queryString = "" }) {
  const [content, setContent] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const contentRef = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      setLoading(true);
      setError("");
      try {
        const path = caseId ? `/api/ui/cases/${encodeURIComponent(caseId)}` : "/api/ui/cases";
        const response = await fetch(`${path}${queryString ? `?${queryString}` : ""}`, {
          credentials: "same-origin", cache: "no-store", signal: controller.signal,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Não foi possível carregar os casos.");
        const html = result.html || "";
        setContent(caseId ? html : prepareCasesDocument(html).body.innerHTML);
      } catch (reason) {
        if (reason.name !== "AbortError") setError(reason.message);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    load();
    return () => controller.abort();
  }, [caseId, queryString, retry]);

  useEffect(() => {
    if (caseId || !content) return;
    const root = contentRef.current;
    if (!root) return;

    const cardFor = target => target instanceof Element ? target.closest(".case-card[data-toggle-url]") : null;
    const interactive = target => target.closest("button, input, select, textarea, label, form, summary, details, .case-inline");

    const navigateCard = async (card, url) => {
      if (!card || card.dataset.loading === "true") return;
      card.dataset.loading = "true";
      card.classList.add("is-loading");
      try {
        const next = new URL(url, window.location.href);
        const response = await fetch(`/api/ui/cases${next.search}`, { credentials: "same-origin", cache: "no-store" });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Não foi possível abrir o caso.");
        const parsed = prepareCasesDocument(result.html);
        const replacement = parsed.getElementById(card.id);
        if (!replacement) throw new Error("O caso não foi encontrado.");
        root.querySelectorAll(".case-card.is-expanded").forEach(previous => {
          if (previous === card) return;
          previous.classList.remove("is-expanded");
          previous.setAttribute("aria-expanded", "false");
          previous.querySelector(".case-inline")?.remove();
          const action = previous.querySelector(".case-card-actions > a.btn");
          if (action) { action.textContent = "Abrir"; action.href = previous.querySelector(".case-title-link")?.href || action.href; }
        });
        card.replaceWith(replacement);
        window.history.pushState({ caseCard: replacement.id }, "", next);
        if (replacement.classList.contains("is-expanded")) replacement.scrollIntoView({ block: "nearest", behavior: "smooth" });
      } catch {
        window.location.assign(url);
      }
    };

    const onClick = event => {
      const card = cardFor(event.target);
      if (!card || !root.contains(card) || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target.closest(".case-title-link, .case-card-actions > a.btn");
      if (interactive(event.target) && !link) return;
      event.preventDefault();
      navigateCard(card, link?.href || card.dataset.toggleUrl);
    };
    const onKeydown = event => {
      const card = cardFor(event.target);
      if (!card || !root.contains(card) || event.target !== card || (event.key !== "Enter" && event.key !== " ")) return;
      event.preventDefault();
      navigateCard(card, card.dataset.toggleUrl);
    };
    const onPopstate = () => window.location.reload();
    root.addEventListener("click", onClick);
    root.addEventListener("keydown", onKeydown);
    window.addEventListener("popstate", onPopstate);
    return () => {
      root.removeEventListener("click", onClick);
      root.removeEventListener("keydown", onKeydown);
      window.removeEventListener("popstate", onPopstate);
    };
  }, [caseId, content]);

  return <>
    
    <section className="container-fluid page-content cases-page">
      {loading && <div className="case-page-feedback" role="status"><span className="spinner-border spinner-border-sm" aria-hidden="true"/>Carregando sua fila de casos…</div>}
      {error && <div className="case-page-feedback is-error" role="alert"><span>{error}</span>{error.includes("sessão") ? <a href="/entrar">Entrar novamente</a> : <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => setRetry(value => value + 1)}>Tentar novamente</button>}</div>}
      {!loading && !error && <div ref={contentRef} className="cases-content" dangerouslySetInnerHTML={{ __html: content }} />}
    </section>
  </>;
}
