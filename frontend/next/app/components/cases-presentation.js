// Arrange the existing cases HTML while preserving its form fields, actions and links.
export function prepareCasesDocument(html) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const workspace = doc.querySelector(".cases-workspace");
  if (!workspace) return doc;

  const element = (tag, className, text) => {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  };
  const icon = name => {
    const paths = {
      plus: "M12 5v14M5 12h14",
      filter: "M4 7h16M7 12h10M10 17h4",
      chevron: "m8 10 4 4 4-4",
      clipboard: "M9 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3M9 3h6v4H9zM8 12h8M8 16h5",
    };
    const svg = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
    for (const [key, value] of Object.entries({viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.7", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true"})) svg.setAttribute(key, value);
    const path = doc.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", paths[name]);
    svg.append(path);
    return svg;
  };
  const link = (label, href, className = "") => {
    const node = element("a", className, label);
    node.setAttribute("href", href);
    return node;
  };

  const switchRow = workspace.querySelector(".case-switch-row");
  const nav = switchRow?.querySelector(".case-switches");
  const createButton = switchRow?.querySelector(":scope > a");
  const createHref = createButton?.getAttribute("href");
  const description = workspace.querySelector(":scope > p:not(.eyebrow):not(.case-toast)");
  const intro = element("div", "cases-intro");
  if (description) intro.append(description);
  if (createButton) {
    createButton.className = "btn btn-primary case-primary-action";
    createButton.prepend(icon("plus"));
    intro.append(createButton);
  }
  workspace.prepend(intro);
  const createPanel = workspace.querySelector(".case-create-inline");
  if (createPanel) intro.after(createPanel);

  const groups = nav?.querySelectorAll(".case-switch-group");
  if (groups?.length) {
    const scope = groups[0];
    scope.classList.add("case-scope-group");
    scope.setAttribute("role", "group");
    scope.setAttribute("aria-label", "Responsabilidade dos casos");
    const scopeRow = element("div", "case-scope-row");
    scopeRow.append(element("span", "case-control-caption", "Mostrar"), scope);
    const views = element("nav", "case-view-tabs");
    views.setAttribute("aria-label", "Situação da fila");
    if (groups[1]) views.append(...Array.from(groups[1].children));
    const archive = nav.querySelector(":scope > a");
    if (archive) views.append(archive);
    for (const selected of [...scope.querySelectorAll("a.selected"), ...views.querySelectorAll("a.selected")]) selected.setAttribute("aria-current", "page");
    switchRow.replaceChildren(scopeRow, views);
  }

  const form = workspace.querySelector(".case-filter");
  let activeFilters = 0;
  let clearHref = "/central-dp/casos";
  let scope = "mine";
  let view = "active";
  if (form) {
    scope = form.querySelector('[name="scope"]')?.value || "mine";
    view = form.querySelector('[name="view"]')?.value || "active";
    clearHref = `?scope=${encodeURIComponent(scope)}&view=${encodeURIComponent(view)}`;
    const labels = Array.from(form.querySelectorAll(":scope > label"));
    const main = element("div", "case-filter-main");
    const extra = element("div", "case-filter-extra-grid");
    let extraCount = 0;
    for (const label of labels) {
      const control = label.querySelector("input, select");
      if (!control) continue;
      const active = Boolean(control.value && (control.name !== "period" || control.value !== "all"));
      activeFilters += Number(active);
      if (["q", "status", "responsible"].includes(control.name)) {
        label.classList.add(`case-filter-${control.name}`);
        if (control.name === "q") {
          label.firstChild.textContent = "Buscar caso";
          control.setAttribute("placeholder", "Título, descrição ou palavra-chave");
          control.setAttribute("type", "search");
        }
        main.append(label);
      } else {
        extra.append(label);
        extraCount += Number(active);
      }
    }
    const header = element("div", "case-filter-heading");
    const heading = element("h2", "", "Filtrar casos");
    heading.prepend(icon("filter"));
    header.append(heading);
    const detail = element("details", "case-filter-extra");
    detail.open = extraCount > 0;
    const summary = element("summary");
    summary.append(element("span", "case-more-label", "Mais filtros"));
    if (extraCount) summary.append(element("b", "case-filter-count", String(extraCount)));
    summary.append(element("span", "case-more-hint", "Período, cliente, prioridade e outros"), icon("chevron"));
    detail.append(summary, extra);
    const footer = element("div", "case-filter-footer");
    footer.append(element("span", "case-filter-applied", activeFilters ? `${activeFilters} ${activeFilters === 1 ? "filtro aplicado" : "filtros aplicados"}` : "Use os filtros para encontrar uma demanda."));
    const actions = element("div", "case-filter-actions");
    if (activeFilters) actions.append(link("Limpar filtros", clearHref, "case-clear-link"));
    const submit = form.querySelector('button');
    if (submit) {
      submit.className = "btn btn-primary";
      submit.textContent = "Aplicar filtros";
      submit.setAttribute("type", "submit");
      actions.append(submit);
    }
    footer.append(actions);
    form.append(header, main, detail, footer);
    form.setAttribute("aria-label", "Filtros dos casos");
  }

  const cards = Array.from(workspace.querySelectorAll(":scope > .case-card"));
  const empty = Array.from(workspace.querySelectorAll(":scope > section.card")).find(node => node.textContent.includes("Nenhum caso corresponde"));
  const results = element("section", "case-results");
  results.setAttribute("aria-labelledby", "case-results-title");
  const resultsHead = element("div", "case-results-heading");
  const resultsTitle = element("h2", "", ({active: "Em acompanhamento", done: "Casos concluídos", deleted: "Casos arquivados"})[view] || "Casos");
  resultsTitle.id = "case-results-title";
  resultsHead.append(resultsTitle, element("span", "case-results-count", `${cards.length} ${cards.length === 1 ? "caso" : "casos"}`));
  results.append(resultsHead, ...cards);
  empty?.remove();
  if (!cards.length) {
    const blank = element("div", "case-empty");
    const badge = element("span", "case-empty-icon");
    badge.append(icon("clipboard"));
    const title = activeFilters ? "Nenhum caso encontrado" : ({active: "Nenhum caso por aqui", done: "Nenhum caso concluído", deleted: "Nenhum caso arquivado"})[view];
    blank.append(badge, element("h3", "", title), element("p", "", activeFilters ? "Ajuste a busca ou limpe os filtros para consultar outras demandas." : "As demandas desta visualização aparecerão aqui, com responsáveis, prazos e checklists."));
    const actions = element("div", "case-empty-actions");
    if (activeFilters) actions.append(link("Limpar filtros", clearHref, "btn btn-outline-primary"));
    else if (scope === "mine" && groups?.[0]?.querySelector("a[href*='scope=team']")) actions.append(link("Ver fila da equipe", `?scope=team&view=${encodeURIComponent(view)}`, "btn btn-outline-primary"));
    else if (view === "active" && createHref) actions.append(link("Criar caso", createHref, "btn btn-outline-primary"));
    blank.append(actions);
    results.append(blank);
  }
  workspace.append(results);
  return doc;
}
