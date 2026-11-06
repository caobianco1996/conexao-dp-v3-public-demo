(() => {
  const cardFor = (target) => target instanceof Element ? target.closest(".case-card[data-toggle-url]") : null;
  const isInteractive = (target) => target.closest("button, input, select, textarea, label, form, summary, details, .case-inline");

  const syncExpandedCards = (openedCard) => {
    document.querySelectorAll(".case-card.is-expanded").forEach((card) => {
      if (card === openedCard) return;
      card.classList.remove("is-expanded");
      card.setAttribute("aria-expanded", "false");
      card.setAttribute("aria-label", card.getAttribute("aria-label")?.replace(/^Recolher/, "Abrir") || "Abrir caso");
      card.querySelector(".case-inline")?.remove();
      const action = card.querySelector(".case-card-actions > a.btn");
      const titleLink = card.querySelector(".case-title-link");
      if (titleLink) card.dataset.toggleUrl = titleLink.href;
      if (action) action.textContent = "Abrir";
    });
  };

  const navigateCard = async (card, url) => {
    if (!card || card.dataset.loading === "true") return;
    card.dataset.loading = "true";
    card.classList.add("is-loading");
    try {
      const response = await fetch(url, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" });
      if (!response.ok) throw new Error("Não foi possível carregar o caso.");
      const html = await response.text();
      const documentFromResponse = new DOMParser().parseFromString(html, "text/html");
      const replacement = documentFromResponse.getElementById(card.id);
      if (!replacement) throw new Error("O caso não foi encontrado na resposta.");
      const wasOpening = replacement.classList.contains("is-expanded");
      syncExpandedCards(wasOpening ? card : null);
      card.replaceWith(replacement);
      history.pushState({ caseCard: replacement.id }, "", url);
      if (wasOpening) replacement.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (error) {
      // Fallback preserves access if fetch is unavailable, while keeping the URL functional.
      window.location.assign(url);
    }
  };

  document.addEventListener("click", (event) => {
    const card = cardFor(event.target);
    if (!card || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const toggleLink = event.target.closest(".case-title-link, .case-card-actions > a.btn");
    if (isInteractive(event.target) && !toggleLink) return;
    event.preventDefault();
    navigateCard(card, toggleLink?.href || card.dataset.toggleUrl);
  });

  document.addEventListener("keydown", (event) => {
    const card = cardFor(event.target);
    if (!card || event.target !== card || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    navigateCard(card, card.dataset.toggleUrl);
  });

  window.addEventListener("popstate", () => window.location.reload());
})();
