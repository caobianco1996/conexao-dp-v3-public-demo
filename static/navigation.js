(() => {
  const sidebar = document.getElementById("global-navigation");
  if (!sidebar) return;
  const nav = sidebar.querySelector(".global-nav-scroll") || sidebar;
  const trigger = document.querySelector(".global-menu-trigger");
  const backdrop = document.querySelector(".global-menu-backdrop");
  const closeButton = sidebar.querySelector(".global-mobile-close");
  const search = sidebar.querySelector("#global-nav-search");
  const empty = sidebar.querySelector(".global-nav-empty");
  const groups = [...sidebar.querySelectorAll("[data-nav-group]")];
  const storageKey = "dp.sidebar.groups.v3";
  let saved = {};
  try {
    const stored = JSON.parse(sessionStorage.getItem(storageKey) || "{}");
    if (stored && typeof stored === "object" && !Array.isArray(stored)) saved = stored;
  } catch { /* optional */ }
  const normalize = value => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
    .replace(/[-×·]/g, "").replace(/\s+/g, " ").trim();
  groups.forEach(group => {
    const id = group.dataset.navGroup;
    group.open = group.dataset.active === "true" || saved[id] === true;
    saved[id] = group.open;
    group.addEventListener("toggle", () => {
      if (search?.value.trim()) return;
      saved[id] = group.open;
      try { sessionStorage.setItem(storageKey, JSON.stringify(saved)); } catch { /* optional */ }
    });
  });
  const filter = () => {
    const query = normalize(search?.value.trim());
    const terms = query.split(" ").filter(Boolean);
    const matchesSearch = value => terms.every(term => normalize(value).includes(term));
    let matches = 0;
    const home = sidebar.querySelector(".global-home-link");
    if (home) {
      home.hidden = !matchesSearch(home.dataset.navLabel);
      if (!home.hidden) matches++;
    }
    groups.forEach(group => {
      const groupLabel = group.querySelector("summary > span")?.textContent || "";
      let visible = 0;
      group.querySelectorAll(".global-nav-links a").forEach(link => {
        link.hidden = !matchesSearch(groupLabel + " " + link.dataset.navLabel);
        if (!link.hidden) visible++;
      });
      group.hidden = visible === 0;
      group.open = query ? visible > 0 : saved[group.dataset.navGroup] === true;
      matches += visible;
    });
    if (empty) empty.hidden = matches > 0;
  };
  search?.addEventListener("input", filter);
  search?.addEventListener("keydown", event => {
    if (event.key === "Escape" && search.value) {
      event.preventDefault(); event.stopPropagation(); search.value = ""; filter();
    }
  });
  try { nav.scrollTop = Number(sessionStorage.getItem("dp.sidebar.scroll") || 0); } catch { /* optional */ }
  nav.addEventListener("scroll", () => {
    try { sessionStorage.setItem("dp.sidebar.scroll", String(nav.scrollTop)); } catch { /* optional */ }
  }, { passive: true });
  requestAnimationFrame(() => sidebar.querySelector('a[aria-current="page"]')?.scrollIntoView({ block: "nearest" }));
  let previousFocus = null;
  let previousOverflow = "";
  const setOpen = open => {
    sidebar.classList.toggle("is-open", open);
    trigger?.setAttribute("aria-expanded", String(open));
    if (backdrop) backdrop.hidden = !open;
    if (open) {
      previousFocus = document.activeElement;
      previousOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      closeButton?.focus();
    } else {
      document.body.style.overflow = previousOverflow;
      if (previousFocus instanceof HTMLElement) previousFocus.focus();
      previousFocus = null;
    }
  };
  trigger?.addEventListener("click", () => setOpen(true));
  backdrop?.addEventListener("click", () => setOpen(false));
  closeButton?.addEventListener("click", () => setOpen(false));
  sidebar.querySelectorAll("nav a").forEach(link => link.addEventListener("click", () => {
    if (sidebar.classList.contains("is-open")) setOpen(false);
  }));
  document.addEventListener("keydown", event => {
    if (!sidebar.classList.contains("is-open")) return;
    if (event.key === "Escape") { event.preventDefault(); setOpen(false); return; }
    if (event.key !== "Tab") return;
    const focusable = [...sidebar.querySelectorAll("a,button,summary,input")]
      .filter(element => !element.disabled && element.getClientRects().length);
    const first = focusable[0], last = focusable.at(-1);
    if (!first) return;
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  window.matchMedia("(min-width: 801px)").addEventListener("change", event => {
    if (event.matches && sidebar.classList.contains("is-open")) setOpen(false);
  });
})();

(() => {
  const snapshot = document.querySelector("[data-sib-status]");
  const label = snapshot?.querySelector("strong");
  if (!snapshot || !label) return;
  let busy = false;
  const update = async () => {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const options = { credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(8000) };
      let response = await fetch("/api/integrations/settings", options);
      let data = response.ok ? await response.json() : null;
      if (!data) {
        response = await fetch("/api/ui/snapshot", { ...options, signal: AbortSignal.timeout(8000) });
        if (!response.ok) throw new Error("snapshot unavailable");
        data = await response.json();
      }
      const active = Boolean(data?.sibAutoRefresh?.running || data?.sibScan?.autoResume ||
        data?.sibScan?.running || data?.sibScan?.inFlight || data?.sibRefresh?.running ||
        data?.sibRefresh?.inFlight || data?.sibIssueJob?.running || data?.sibIssueJob?.inFlight);
      snapshot.classList.toggle("is-active", active);
      snapshot.classList.toggle("is-idle", !active);
      label.textContent = data?.sibScan?.retrying ? "Reconectando SIB" : active ? "Consultando SIB" :
        data?.sibScan?.done ? "Faixa consultada" : data?.sibScan?.started ? "Consulta pausada" : "Pronto para consultar";
    } catch {
      snapshot.classList.remove("is-active");
      snapshot.classList.add("is-idle");
      label.textContent = "Status indisponível";
    } finally { busy = false; }
  };
  update();
  const interval = setInterval(update, 5000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) update(); });
  window.addEventListener("pagehide", () => clearInterval(interval), { once: true });
})();

(() => {
  let activeCard = null;
  let returnFocus = null;
  let keyHandler = null;
  let openedFromList = false;
  const updateCard = (card, expanded) => {
    if (!card) return;
    card.classList.toggle("is-selected", expanded);
    card.setAttribute("aria-expanded", String(expanded));
    const link = card.querySelector(".process-open");
    if (link) link.textContent = expanded ? "Recolher" : "Abrir";
  };
  const closeDialog = () => {
    const backdrop = document.querySelector("[data-detail-backdrop]");
    if (backdrop) backdrop.remove();
    document.body.classList.remove("has-process-dialog");
    if (keyHandler) document.removeEventListener("keydown", keyHandler);
    updateCard(activeCard, false);
    const focused = returnFocus;
    activeCard = null; returnFocus = null; keyHandler = null;
    if (focused?.isConnected) focused.focus();
    const current = new URL(window.location.href);
    if (openedFromList && (current.searchParams.has("id") || current.searchParams.has("local"))) {
      openedFromList = false;
      window.history.back();
    } else if (current.searchParams.has("id") || current.searchParams.has("local")) {
      current.searchParams.delete("id"); current.searchParams.delete("local");
      window.history.replaceState({}, "", current.pathname + current.search + current.hash);
    }
  };
  window.addEventListener("popstate", () => {
    const current = new URL(window.location.href);
    if (activeCard && !current.searchParams.has("id") && !current.searchParams.has("local")) {
      openedFromList = false;
      closeDialog();
    }
  });
  const activateDialog = (backdrop, card = null) => {
    const dialog = backdrop.querySelector("[data-process-dialog]");
    if (!dialog) return;
    if (document.querySelector("[data-detail-backdrop]") !== backdrop) document.querySelector("[data-detail-backdrop]")?.remove();
    if (backdrop.parentElement !== document.body) document.body.append(backdrop);
    activeCard = card;
    returnFocus = document.activeElement;
    updateCard(card, true);
    document.body.classList.add("has-process-dialog");
    const closeButton = dialog.querySelector("[data-close-detail]");
    closeButton?.addEventListener("click", closeDialog);
    backdrop.addEventListener("click", (event) => { if (event.target === backdrop) closeDialog(); });
    keyHandler = (event) => {
      if (event.key === "Escape") { event.preventDefault(); closeDialog(); return; }
      if (event.key !== "Tab") return;
      const focusable = [...dialog.querySelectorAll('a[href],button:not(:disabled),summary,[tabindex]:not([tabindex="-1"])')]
        .filter((element) => element.getClientRects().length);
      const first = focusable[0], last = focusable.at(-1);
      if (!first) { event.preventDefault(); dialog.focus(); }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", keyHandler);
    dialog.focus();
  };
  const openDialog = async (card) => {
    if (activeCard === card) { closeDialog(); return; }
    try {
      const response = await fetch(card.dataset.detailHref, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } });
      if (!response.ok) throw new Error("Falha ao carregar o processo");
      const documentHtml = new DOMParser().parseFromString(await response.text(), "text/html");
      const backdrop = documentHtml.querySelector("[data-detail-backdrop]");
      if (!backdrop) throw new Error("Detalhe indisponível");
      window.history.pushState({ processDialog: true }, "", card.dataset.detailHref);
      openedFromList = true;
      activateDialog(backdrop, card);
    } catch {
      const description = card.querySelector(".process-description");
      if (description) description.textContent = "Não foi possível abrir este processo agora. Tente novamente.";
    }
  };
  document.querySelectorAll("[data-process-card]").forEach((card) => {
    card.setAttribute("aria-expanded", "false");
    card.addEventListener("click", (event) => {
      const link = event.target.closest(".process-open");
      if (link) event.preventDefault();
      else if (event.target.closest("a,button,input,select,textarea")) return;
      openDialog(card);
    });
    card.addEventListener("keydown", (event) => {
      if (event.target !== card || (event.key !== "Enter" && event.key !== " ")) return;
      event.preventDefault(); openDialog(card);
    });
  });
  const initialBackdrop = document.querySelector("[data-detail-backdrop]");
  if (initialBackdrop) {
    const selected = [...document.querySelectorAll("[data-process-card]")]
      .find((card) => new URL(card.dataset.detailHref, window.location.origin).searchParams.get("id") === new URL(window.location.href).searchParams.get("id"));
    activateDialog(initialBackdrop, selected || null);
  }
})();

(() => {
  const button = document.querySelector(".theme-button");
  if (!button) return;
  const applyTheme = (theme) => {
    document.body.dataset.theme = theme;
    const dark = theme === "dark";
    button.setAttribute("aria-pressed", String(dark));
    button.setAttribute("aria-label", dark ? "Ativar modo claro" : "Ativar modo escuro");
    button.title = dark ? "Ativar modo claro" : "Ativar modo escuro";
  };
  let saved = "light";
  try { saved = localStorage.getItem("conexao-dp-theme") || localStorage.getItem("conexao-dp.theme") || "light"; } catch { /* optional */ }
  applyTheme(saved === "dark" ? "dark" : "light");
  button.addEventListener("click", () => {
    const theme = document.body.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(theme);
    try { localStorage.setItem("conexao-dp-theme", theme); localStorage.setItem("conexao-dp.theme", theme); } catch { /* optional */ }
  });
})();

(() => {
  const toggle = document.querySelector(".quick-calc-toggle");
  const panel = document.querySelector(".quick-calc");
  const display = panel?.querySelector(".quick-calc-display");
  if (!toggle || !panel || !display) return;
  let entry = "0", accumulator = null, pending = null, startNext = true;
  const show = (value) => {
    if (!Number.isFinite(value)) { entry = "Erro"; startNext = true; }
    else { entry = String(Number(value.toPrecision(12))); startNext = true; }
    display.textContent = entry === "Erro" ? entry : Number(entry).toLocaleString("pt-BR", {maximumFractionDigits: 10});
  };
  const value = () => Number(entry.replace(",", ".")) || 0;
  const calculate = (left, operator, right) => operator === "+" ? left + right : operator === "-" ? left - right : operator === "*" ? left * right : right === 0 ? NaN : left / right;
  const enterDigit = (digit) => {
    if (entry === "Erro" || startNext) { entry = digit; startNext = false; }
    else if (entry.replace(/\D/g, "").length < 14) entry = entry === "0" ? digit : entry + digit;
    display.textContent = Number(entry.replace(",", ".")).toLocaleString("pt-BR", {maximumFractionDigits: 10});
  };
  const press = (key) => {
    if (/^\d$/.test(key)) { enterDigit(key); return; }
    if (key === "decimal") {
      if (entry === "Erro" || startNext) { entry = "0,"; startNext = false; }
      else if (!entry.includes(",")) entry += ",";
      display.textContent = entry; return;
    }
    if (key === "clear") { entry = "0"; accumulator = null; pending = null; startNext = true; display.textContent = entry; return; }
    if (key === "backspace") {
      if (entry !== "Erro" && !startNext) entry = entry.length > 1 ? entry.slice(0, -1) : "0";
      if (entry === "-" || entry === "-0") entry = "0";
      display.textContent = entry; return;
    }
    if (key === "sign") { if (entry !== "Erro" && value() !== 0) entry = String(-value()).replace(".", ","); display.textContent = entry; return; }
    if (key === "percent") { if (entry !== "Erro") show(value() / 100); return; }
    if (["+", "-", "*", "/"].includes(key)) {
      if (pending && !startNext && accumulator !== null) { accumulator = calculate(accumulator, pending, value()); show(accumulator); }
      else if (accumulator === null || !startNext) accumulator = value();
      pending = key; startNext = true; return;
    }
    if (key === "equals" && pending && accumulator !== null) {
      const result = calculate(accumulator, pending, value());
      show(result); accumulator = null; pending = null;
    }
  };
  const setOpen = (open) => {
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    if (open) panel.querySelector("button")?.focus();
    else toggle.focus();
  };
  toggle.addEventListener("click", () => setOpen(panel.hidden));
  panel.querySelector(".quick-calc-close")?.addEventListener("click", () => setOpen(false));
  panel.addEventListener("click", (event) => {
    const key = event.target.closest("[data-calc-key]")?.dataset.calcKey;
    if (key) press(key);
  });
  document.addEventListener("pointerdown", (event) => {
    if (!panel.hidden && !event.target.closest(".quick-calc-wrap")) setOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (panel.hidden) return;
    const editable = event.target.closest("input,textarea,select,[contenteditable='true']");
    if (event.key === "Escape") { event.preventDefault(); setOpen(false); return; }
    if (editable) return;
    const key = /^\d$/.test(event.key) ? event.key : ({".": "decimal", ",": "decimal", "/": "/", "*": "*", "-": "-", "+": "+", "Enter": "equals", "=": "equals", "Backspace": "backspace", "%": "percent"})[event.key];
    if (key) { event.preventDefault(); press(key); }
  });
})();
