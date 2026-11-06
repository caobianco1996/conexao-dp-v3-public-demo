"use client";

import { createElement, useEffect, useMemo, useState } from "react";
import { AvatarPortrait, PROFILE_AVATAR_KEY, PROFILE_AVATARS, profileAvatarById, saveProfileAvatar, saveProfileMood, useProfileAvatar } from "./profile-avatar";
import navigation from "../../../../static/navigation.json";
import styles from "./home.module.css";

const labels = { open: "Em aberto", overdue: "Vencidos", upcoming: "Próximos 7 dias", checklist: "Checklists pendentes", waiting: "Aguardando retorno", completed: "Concluídos" };
const metricIcons = { open: "files", overdue: "alert", upcoming: "calendar", checklist: "checklist", waiting: "hourglass", completed: "badgeCheck" };
function HomeIcon({ name, className = "" }) {
  return <svg className={className} viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {(navigation.icons[name] || navigation.icons.home).map((shape, index) => createElement(shape.tag, { ...shape.attrs, key: index }))}
  </svg>;
}
const Arrow = () => <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h14m-5-5 5 5-5 5"/></svg>;
const processStatusLabels = { DRAFT: "Rascunho", ARCHIVED: "Arquivado", PUBLISHED: "Publicado", ACTIVE: "Ativo", INACTIVE: "Inativo", LOCAL: "Cadastro local" };
const processStatus = value => processStatusLabels[String(value || "").toUpperCase()] || value || "Não informado";
const stepText = step => typeof step === "string" ? step : step?.title || step?.detail || step?.name || "";

function WorkbookSourceSection({ section, canViewOriginal }) {
  const [showImages, setShowImages] = useState(false);
  return <details className="process-workbook-section"><summary>{section.title}<small>{section.sourceRange}</small></summary><div>
    <p className="process-muted">{(section.notes || []).join(" ")}</p>
    <div className="process-workbook-lines">{(section.lines || []).map((line,index)=><p key={index}><small>{line.cell}</small><span>{line.text}</span></p>)}</div>
    {canViewOriginal && section.images?.length > 0 && <details className="process-workbook-captures" onToggle={event=>setShowImages(event.currentTarget.open)}><summary>Capturas da origem ({section.images.length}) · administração</summary>{showImages && <div>{section.images.map(image=><a key={image.name} href={image.href} target="_blank" rel="noreferrer"><img src={image.href} alt={`Captura da orientação ${section.title}`} loading="lazy"/></a>)}</div>}</details>}
  </div></details>;
}

function ProcessCard({ item, isOpen, onToggle }) {
  const rawSteps = item.content?.steps || item.steps;
  const rawChecklist = item.content?.content?.checklist || item.content?.checklist || item.checklist;
  const steps = Array.isArray(rawSteps) ? rawSteps : [];
  const checklist = Array.isArray(rawChecklist) ? rawChecklist : [];
  const hasWorkbook = Boolean(item.workbook?.sections?.length);
  const description = hasWorkbook && String(item.description || "").includes("ainda sem detalhamento textual suficiente") ? "Orientações da planilha disponíveis para consulta." : item.description;
  return <article className={`card process-card${isOpen ? " is-open" : ""}`}>
    <button type="button" className="process-summary" onClick={onToggle} aria-expanded={isOpen} aria-controls={`process-detail-${item.id}`} aria-label={`${isOpen ? "Recolher" : "Abrir"} processo: ${item.title}`}>
      <span className="process-icon"><HomeIcon name="workflow"/></span>
      <span className="process-copy"><span className="eyebrow">{item.category || "Geral"} · {item.system || "Interno"}</span><span className="process-title">{item.title}</span><span className="process-description">{description || "Consulte as orientações e etapas deste processo."}</span><span className="process-meta"><span className="process-status" data-status={String(item.status || "").toUpperCase()}>{processStatus(item.status)}</span><span>Versão {item.current_version || 1}</span></span></span>
      <span className="process-toggle"><span>{isOpen ? "Recolher" : "Ver detalhes"}</span><svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></span>
    </button>
    {isOpen && <div className="process-detail" id={`process-detail-${item.id}`}>
      <dl className="process-facts">{[["Sistema",item.system],["Origem",item.source],["Vigência",item.valid_from],["Empresa",item.client_name],["Unidade",item.unit_name]].filter(([,value])=>value).map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      {(item.content?.whenToUse || item.content?.objective || !hasWorkbook) && <section className="process-detail-block"><h3>Objetivo</h3><p>{item.content?.whenToUse || item.content?.objective || "Objetivo não detalhado neste cadastro."}</p></section>}
      {(steps.length > 0 || checklist.length > 0 || !hasWorkbook) && <div className="process-detail-grid">
        <section className="process-detail-block"><h3><HomeIcon name="workflow"/>Passo a passo</h3>{steps.length ? <ol>{steps.map((step,index)=><li key={index}>{stepText(step)}</li>)}</ol> : <p className="process-muted">As etapas ainda não foram informadas.</p>}</section>
        <section className="process-detail-block"><h3><HomeIcon name="checklist"/>Checklist</h3>{checklist.length ? <ul>{checklist.map((step,index)=><li key={index}>{stepText(step)}</li>)}</ul> : <p className="process-muted">Nenhum item de checklist informado.</p>}</section>
      </div>}
      {item.workbook?.sections?.length > 0 && <section className="process-workbook"><h3>Orientações da planilha</h3>{item.workbook.sections.map((section,index)=><WorkbookSourceSection key={index} section={section} canViewOriginal={item.workbook.canViewOriginal}/>)}{item.workbook.canViewOriginal && <a className="process-workbook-download" href={`/api/ui/sector/source/${item.workbook.sourceId}/download`}>Baixar planilha de origem · acesso restrito</a>}</section>}
      <div className="process-detail-actions"><a className="btn btn-outline-primary" href="/central-dp/manuais"><HomeIcon name="book"/>Abrir manuais</a><a className="btn btn-primary" href={`/central-dp/casos?create=1&from_process=${encodeURIComponent(item.id)}#novo`}>Criar caso a partir deste processo<Arrow/></a></div>
    </div>}
  </article>;
}
const escDate = value => value ? new Date(`${String(value).slice(0,10)}T12:00:00`).toLocaleDateString("pt-BR") : "Sem prazo";
const moodChoices = [
  { id: "irritated", label: "Irritado", face: "😠" },
  { id: "confused", label: "Confuso", face: "😕" },
  { id: "happy", label: "Alegre", face: "😁" },
  { id: "melancholy", label: "Melancólico", face: "😔" },
  { id: "anxious", label: "Ansioso", face: "😰" },
  { id: "serene", label: "Sereno", face: "😌" },
  { id: "enthusiastic", label: "Entusiasmado", face: "🤩" },
  { id: "exhausted", label: "Esgotado", face: "🥱" },
];
const moodForLegacyStatus = { ok: "serene", warning: "confused", critical: "anxious" };
const getSaoPauloGreeting = (date = new Date()) => {
  const hour = Number(new Intl.DateTimeFormat("pt-BR", { hour: "numeric", hourCycle: "h23", timeZone: "America/Sao_Paulo" }).format(date));
  return hour >= 5 && hour < 12 ? "Bom dia" : hour >= 12 && hour < 18 ? "Boa tarde" : "Boa noite";
};
const saoPauloDateKey = (date = new Date()) => {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/Sao_Paulo", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
};

function WelcomeMascot({ avatar, mood, label }) {
  return <div className="welcome-mascot-frame"><img className="welcome-mascot-image" src={`/avatars/welcome-mascots/${avatar.assetPrefix}-${mood}.png`} alt={`${avatar.label}, expressão ${label.toLowerCase()}, rosto e parte superior do corpo`} loading="eager"/></div>;
}

export default function CentralPage({ view, selectedId = "" }) {
  const isHome = view === "inicio";
  const [data, setData] = useState(null), [error, setError] = useState(""), [busy, setBusy] = useState(true);
  const [scope, setScope] = useState("mine"), [filters, setFilters] = useState({q:"",category:"",system:"",client:"",unit:"",status:""}), [opened, setOpened] = useState("");
  const [advancedFiltersOpen, setAdvancedFiltersOpen] = useState(false);
  const [avatarChooserOpen, setAvatarChooserOpen] = useState(false), [moodChoice, setMoodChoice] = useState("serene");
  const [simulatedDeadlinePosition, setSimulatedDeadlinePosition] = useState(null);
  const [profileIdentity, setProfileIdentity] = useState(null), [profileName, setProfileName] = useState("");
  const [greeting, setGreeting] = useState(""), [todayLabel, setTodayLabel] = useState("");
  const [profileCsrf, setProfileCsrf] = useState(""), [avatarError, setAvatarError] = useState("");
  const { avatar, chooseAvatar } = useProfileAvatar(profileIdentity);

  useEffect(() => {
    let active = true;
    setBusy(true); setError("");
    const query = isHome ? `?scope=${scope}` : "";
    fetch(`/api/ui/${isHome ? "home" : "processes"}${query}`, { cache: "no-store" })
      .then(async response => { const payload = await response.json(); if (!response.ok) throw new Error(payload.error || "Falha ao carregar a Central DP."); return payload; })
      .then(payload => { if(active) setData(payload); })
      .catch(reason => { if(active) setError(reason.message); })
      .finally(() => { if(active) setBusy(false); });
    return () => { active = false; };
  }, [isHome, scope]);
  useEffect(() => {
    const updateGreeting = () => {
      setGreeting(getSaoPauloGreeting());
      setTodayLabel(new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", weekday: "long", day: "numeric", month: "long" }).format(new Date()));
    };
    updateGreeting();
    const timer = window.setInterval(updateGreeting, 60_000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!isHome) return;
    let active = true;
    fetch("/api/chat", { credentials: "same-origin", cache: "no-store" })
      .then(response => response.ok ? response.json() : null)
      .then(async payload => {
        if (!active || payload?.userId == null) return;
        const identity = String(payload.userId);
        setProfileIdentity(identity);
        setProfileName(String(payload.userName || ""));
        setProfileCsrf(payload.csrf || "");
        try {
          const moodKey = `conexao-dp-home-mood:${identity}`;
          let savedMood = JSON.parse(window.localStorage.getItem(moodKey) || "null");
          if (!savedMood) {
            savedMood = JSON.parse(window.localStorage.getItem("conexao-dp-home-mood:current") || "null");
            if (savedMood) window.localStorage.setItem(moodKey, JSON.stringify(savedMood));
          }
          const savedChoice = savedMood?.day === saoPauloDateKey() ? savedMood.mood : null;
          const localChoice = moodChoices.some(choice => choice.id === savedChoice) ? savedChoice : moodForLegacyStatus[savedChoice] || null;
          const initialMood = localChoice || (moodChoices.some(choice => choice.id === payload.moodId) ? payload.moodId : "serene");
          setMoodChoice(initialMood);
          saveProfileMood(initialMood, initialMood, identity);
          if (initialMood !== payload.moodId && payload.csrf) {
            fetch("/api/chat/profile", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf: payload.csrf, moodId: initialMood }) }).catch(() => {});
          }
        } catch { setMoodChoice("serene"); }
        let localAvatar = null;
        try { localAvatar = window.localStorage.getItem(`${PROFILE_AVATAR_KEY}:${identity}`); } catch {}
        const initialAvatar = profileAvatarById(localAvatar || payload.avatarId).id;
        saveProfileAvatar(initialAvatar, identity);
        if (localAvatar && initialAvatar !== payload.avatarId && payload.csrf) {
          fetch("/api/chat/profile", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf: payload.csrf, avatarId: initialAvatar }) }).catch(() => {});
        }
      })
      .catch(() => {});
    return () => { active = false; };
  }, [isHome]);
  useEffect(() => { if (!isHome && selectedId) setOpened(selectedId); }, [isHome, selectedId]);

  const visible = useMemo(() => (data?.items || []).filter(item => {
    const haystack = [item.title,item.description,item.category,item.system,item.client_name,item.unit_name].join(" ").toLowerCase();
    return (!filters.q || haystack.includes(filters.q.toLowerCase())) && ["category","system","client_name","unit_name","status"].every((key,index) => !filters[["category","system","client","unit","status"][index]] || String(item[key] || "") === filters[["category","system","client","unit","status"][index]]);
  }), [data, filters]);

  const overdueCount = Number(data?.metrics?.overdue || 0);
  const upcomingCount = Number(data?.metrics?.upcoming || 0);
  const actualFocusStatus = overdueCount > 0 ? "critical" : upcomingCount > 0 ? "warning" : "ok";
  const simulatedStatus = simulatedDeadlinePosition === null ? actualFocusStatus : simulatedDeadlinePosition < 34 ? "ok" : simulatedDeadlinePosition < 67 ? "warning" : "critical";
  const personalMood = moodChoices.find(choice => choice.id === moodChoice) || moodChoices[1];
  const deadlineMood = { ok: { face: "😊", title: "Tudo em dia" }, warning: { face: "😟", title: "Atenção aos prazos" }, critical: { face: "😰", title: "Prazo crítico" } }[simulatedStatus];
  const firstName = profileName.trim().split(/\s+/)[0];
  const greetingTitle = `${greeting || "Olá"}${firstName ? `, ${firstName}` : ""}!`;
  const welcomeCopy = ({
    irritated: "Dia daqueles? Tudo bem sentir isso. Vamos devagar e cuidar de uma coisa por vez.",
    confused: "Quando tudo embaralha, uma pausa ajuda. Escolha uma tarefa pequena para começar.",
    happy: "Que bom ter você por aqui! Leve essa energia boa para o seu dia.",
    melancholy: "Um dia mais quieto também merece gentileza. Faça as coisas no seu ritmo.",
    anxious: "Respire fundo. Você pode olhar uma demanda por vez, sem antecipar todas as outras.",
    serene: "Um passo de cada vez. Que seu dia siga leve e equilibrado.",
    enthusiastic: "Essa energia inspira! Vamos transformar a empolgação em uma próxima etapa.",
    exhausted: "Parece que o tanque está baixo. Faça uma pausa possível e retome no seu ritmo.",
  })[moodChoice];

  const handleMoodChoice = mood => {
    setMoodChoice(mood);
    saveProfileMood(mood, mood, profileIdentity || "current");
    if (profileCsrf) fetch("/api/chat/profile", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf: profileCsrf, avatarId: avatar.id, moodId: mood }) }).catch(() => {});
  };

  const handleAvatarChoice = async id => {
    chooseAvatar(id);
    setAvatarError("");
    if (!profileCsrf) return;
    try {
      const response = await fetch("/api/chat/profile", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf: profileCsrf, avatarId: id, moodId: moodChoice }) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Falha ao salvar a miniatura.");
    } catch (reason) {
      setAvatarError(`A miniatura foi escolhida neste navegador, mas não sincronizou com o Chat: ${reason.message}`);
    }
  };

  const filterCount = Object.values(filters).filter(Boolean).length;
  const advancedCount = [filters.system, filters.client, filters.unit].filter(Boolean).length;
  const clearProcessFilters = () => setFilters({ q:"",category:"",system:"",client:"",unit:"",status:"" });
  const processFilter = (key, label) => <label key={key}>{label}<select className="form-select" value={filters[key]} onChange={event=>setFilters(current=>({...current,[key]:event.target.value}))}><option value="">{key === "category" ? "Todas as categorias" : key === "status" ? "Todas as situações" : "Todos"}</option>{(data?.choices?.[key] || []).map(value=><option key={value} value={value}>{key === "status" ? processStatus(value) : value}</option>)}</select></label>;

  return <>
    
    <section className={`container-fluid page-content ${isHome ? styles.page : "processes-page"}`}>{!isHome && <div className="central-page-intro process-page-intro"><div><span className="process-eyebrow">Biblioteca de trabalho</span><p>Encontre orientações, consulte etapas e transforme processos em casos.</p></div><div className="process-detail-actions"><a className="btn btn-outline-primary" href="/rotinas/setor">Organização do setor</a><a className="btn btn-primary" href="#novo-processo"><span aria-hidden="true">+</span>Novo processo</a></div></div>}
      {error && <div className="alert alert-warning" role="alert">{error} {error.includes("sessão") && <a href="/entrar">Entrar</a>}</div>}
      {busy && (!isHome || !data) && <p className="text-secondary" role="status">Carregando dados locais…</p>}
      {!error && data && isHome && <>
        <section className={styles.welcome} aria-labelledby="welcome-card-title">
          <div className={styles.welcomeMain}>
            <div className={styles.welcomeText}>
              <span className={styles.date}>{todayLabel || "Bem-vindo à Central DP"}</span>
              <h2 id="welcome-card-title">{greetingTitle}</h2>
              <p>{welcomeCopy}</p>
              <span className={styles.welcomeNote}>Seu dia, uma demanda de cada vez.</span>
            </div>
            <WelcomeMascot avatar={avatar} mood={moodChoice} label={personalMood.label}/>
          </div>
          <details className={styles.personalize}>
            <summary><span className={styles.personalizeTitle}><HomeIcon name="sparkles"/>Como você está hoje?</span><span className={styles.moodBadge}><span aria-hidden="true">{personalMood.face}</span>{personalMood.label}</span><span className={styles.personalizeAction}>Personalizar <svg className={styles.chevron} viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></span></summary>
            <div className={styles.personalizeBody}>
              <fieldset className={styles.feelings}><legend>Escolha seu humor</legend><div className={styles.feelingOptions}>
                {moodChoices.map(choice => <button key={choice.id} type="button" className={styles.feelingOption} aria-pressed={moodChoice === choice.id} onClick={() => handleMoodChoice(choice.id)}><span aria-hidden="true">{choice.face}</span>{choice.label}</button>)}
              </div><small>A ilustração e sua miniatura no Chat acompanham sua escolha.</small></fieldset>
              <div className={styles.avatarSettings}>
                <div className={styles.avatarHeading}><AvatarPortrait avatarId={avatar.id} size={40}/><div><strong>Seu avatar</strong><small>Também aparece no Chat.</small></div><button type="button" className="btn btn-outline-primary btn-sm" aria-expanded={avatarChooserOpen} aria-controls="home-avatar-options" onClick={() => setAvatarChooserOpen(value => !value)}>{avatarChooserOpen ? "Concluir" : "Alterar"}</button></div>
                {avatarChooserOpen && <div className={styles.avatarOptions} id="home-avatar-options" aria-label="Escolha um avatar">
                  {PROFILE_AVATARS.map(option => <button key={option.id} type="button" className={styles.avatarOption} aria-pressed={avatar.id === option.id} onClick={() => handleAvatarChoice(option.id)}><AvatarPortrait avatarId={option.id} size={38}/><span>{option.label}</span></button>)}
                </div>}
                {avatarError && <small className="focus-avatar-error" role="status">{avatarError}</small>}
              </div>
            </div>
          </details>
        </section>

        <section className={styles.overview} aria-labelledby="home-overview-title" aria-busy={busy}>
          <div className={styles.sectionHeader}>
            <div><span className={styles.eyebrow}>Acompanhamento</span><h2 id="home-overview-title">Resumo do trabalho</h2></div>
            <a className={`btn btn-primary ${styles.createCase}`} href="/central-dp/casos?create=1#novo"><span aria-hidden="true">+</span> Criar caso</a>
          </div>
          <div className={styles.overviewToolbar}>
            <div className={styles.scope} role="group" aria-label="Escopo dos casos"><button type="button" disabled={busy} aria-pressed={scope === "mine"} onClick={() => setScope("mine")}>Atribuídos a mim</button><button type="button" disabled={busy} aria-pressed={scope === "team"} onClick={() => setScope("team")}>Todos da equipe</button></div>
            <span className={styles.scopeCaption} role="status">{busy ? "Atualizando demandas…" : scope === "mine" ? "Suas demandas e pendências" : "Demandas e pendências da equipe"}</span>
          </div>
          <div className={styles.metrics}>{Object.entries(labels).map(([key,label])=><a key={key} className={`${styles.metric} ${styles[key]}`} href={`/central-dp/casos?home_scope=${scope}&home_filter=${key}`}><span className={styles.metricTop}><HomeIcon name={metricIcons[key]}/><Arrow/></span><strong>{data.metrics?.[key] ?? 0}</strong><span>{label}</span></a>)}</div>
        </section>

        <section className={`${styles.deadline} ${styles[simulatedStatus]}`} aria-labelledby="deadline-focus-title">
          <div className={styles.deadlineMain}>
            <span className={styles.deadlineAvatar}><AvatarPortrait avatarId={avatar.id} size={68} mood={simulatedStatus}/><span aria-hidden="true">{deadlineMood.face}</span></span>
            <div className={styles.deadlineText}>
              <span className={styles.eyebrow}>Foco de hoje</span>
              <h2 id="deadline-focus-title">{simulatedDeadlinePosition === null ? deadlineMood.title : `Prévia: ${deadlineMood.title}`}</h2>
              <p>{overdueCount > 0 ? `${overdueCount} demanda(s) vencida(s). Priorize a próxima etapa.` : upcomingCount > 0 ? `${upcomingCount} demanda(s) vencem nos próximos 7 dias. Prepare a próxima etapa.` : "Nenhuma demanda vencida ou próxima do prazo. Sua fila está sob controle."}</p>
            </div>
            <a className={styles.deadlineLink} href={`/central-dp/casos?home_scope=${scope}&home_filter=${overdueCount > 0 ? "overdue" : "upcoming"}`}>{overdueCount > 0 ? "Ver vencidos" : "Ver prazos"}<Arrow/></a>
          </div>
          <details className={styles.deadlinePreview}><summary>Prévia da expressão do avatar <span aria-hidden="true">⌄</span></summary><div className="focus-concern-controls"><label htmlFor="deadline-simulation"><strong>Simular a aproximação de um prazo</strong><small>A prévia muda apenas a expressão do avatar.</small></label><input id="deadline-simulation" type="range" min="0" max="100" step="1" value={simulatedDeadlinePosition ?? ({ ok: 0, warning: 50, critical: 100 }[actualFocusStatus])} onChange={event => setSimulatedDeadlinePosition(Number(event.target.value))} aria-label="Simular a aproximação de um prazo"/><div className="focus-range-labels"><span>😊 Tudo em dia</span><span>😟 Atenção</span><span>😰 Prazo crítico</span></div><div className="focus-range-footer"><small>Prazos reais: {overdueCount} vencido(s) · {upcomingCount} próximo(s).</small>{simulatedDeadlinePosition !== null && <button type="button" className="btn btn-link" onClick={() => setSimulatedDeadlinePosition(null)}>Voltar aos prazos reais</button>}</div></div></details>
        </section>

        <div className={styles.workspace}>
          <section className={styles.panel} aria-labelledby="home-cases-title">
            <div className={styles.panelHead}><div><span className={styles.eyebrow}>Casos e checklists</span><h2 id="home-cases-title">{scope === "mine" ? "Minha fila" : "Fila da equipe"}</h2></div><a href={`/central-dp/casos?home_scope=${scope}&home_filter=active`}>Ver todos <Arrow/></a></div>
            {data.cases?.length ? <div className={styles.caseList}>{data.cases.map(item=><a className={styles.caseRow} key={`${item.source}-${item.source_id}`} href={item.href || "/central-dp/casos"}><span><strong>{item.title}</strong><small>{item.company} · {item.owner_name}</small><small>{escDate(item.due_date)} · {item.checklist_pending || 0} pendência(s)</small></span><Arrow/></a>)}</div> : <div className={styles.empty}><span className={styles.emptyIcon}><HomeIcon name="checklist"/></span><h3>Nenhum caso por aqui</h3><p>{scope === "mine" ? "Você ainda não tem casos atribuídos. Consulte a fila da equipe ou crie um novo caso." : "Nenhum caso em acompanhamento neste escopo."}</p>{scope === "mine" && <button type="button" className="btn btn-outline-primary" onClick={() => setScope("team")}>Ver fila da equipe <Arrow/></button>}</div>}
          </section>
          <section className={styles.panel} aria-labelledby="home-processes-title">
            <div className={styles.panelHead}><div><span className={styles.eyebrow}>Guias de trabalho</span><h2 id="home-processes-title">Processos disponíveis</h2></div><HomeIcon name="workflow"/></div>
            <div className={styles.processList}>{data.processes?.map(item=><a className={styles.processRow} key={item.id} href={`/central-dp/processos?id=${encodeURIComponent(item.id)}`}><span>{item.title}</span><Arrow/></a>)}</div>
            {!data.processes?.length && <p className={styles.panelNote}>Nenhum processo disponível.</p>}
            <a className={styles.panelFooter} href="/central-dp/processos">Consultar todos os processos <Arrow/></a>
          </section>
        </div>
        <section className={styles.shortcuts} aria-labelledby="home-shortcuts-title">
          <div className={styles.sectionHeader}><div><span className={styles.eyebrow}>Acesso rápido</span><h2 id="home-shortcuts-title">Continue sua rotina</h2></div></div>
          <nav className={styles.shortcutGrid} aria-label="Atalhos da página inicial">{[
            { icon: "calendar", title: "Calendário DP", copy: "Compromissos e obrigações", href: "/rotinas/calendario" },
            { icon: "spreadsheet", title: "Relatórios", copy: "Listas e planilhas por empresa", href: "/analytics/relatorios" },
            { icon: "mailClock", title: "Envios automáticos", copy: "Agendamentos de relatórios", href: "/comunicacao/envios-automaticos" },
            { icon: "book", title: "Manuais", copy: "Orientações para o dia a dia", href: "/central-dp/manuais" },
          ].map(item => <a key={item.href} href={item.href}><span className={styles.shortcutIcon}><HomeIcon name={item.icon}/></span><span><strong>{item.title}</strong><small>{item.copy}</small></span><Arrow/></a>)}</nav>
        </section>
      </>}
      {!busy && !error && data && !isHome && <>
        <section className="card process-filter-card" aria-label="Filtros de processos">
          <div className="process-primary-filters"><label className="process-search">Buscar processo<span><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/></svg><input className="form-control" type="search" value={filters.q} onChange={event=>setFilters(current=>({...current,q:event.target.value}))} placeholder="Título ou palavra-chave"/></span></label>{processFilter("category","Categoria")}{processFilter("status","Situação")}</div>
          <div className="process-filter-footer"><button className="process-more-filters" type="button" aria-expanded={advancedFiltersOpen} aria-controls="process-advanced-filters" onClick={()=>setAdvancedFiltersOpen(value=>!value)}><HomeIcon name="layers"/>Mais filtros{advancedCount > 0 && <span className="process-filter-badge">{advancedCount}</span>}<svg className={advancedFiltersOpen ? "is-open" : ""} viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button><span>Sistema, cliente e unidade</span>{filterCount > 0 && <button className="process-clear-filters" type="button" onClick={clearProcessFilters}>Limpar filtros</button>}</div>
          <div className="process-advanced-filters" id="process-advanced-filters" hidden={!advancedFiltersOpen}>{[["system","Sistema"],["client","Cliente"],["unit","Unidade"]].map(([key,label])=>processFilter(key,label))}</div>
        </section>
        <div className="process-results-heading"><h2>Processos disponíveis <span>{visible.length}</span></h2><span role="status">{filterCount ? `${visible.length} de ${data.items?.length || 0} processos · ${filterCount} filtro(s)` : "Organizados por nome"}</span></div>
        <div className="process-list">{visible.map(item=><ProcessCard key={item.id} item={item} isOpen={opened===item.id} onToggle={()=>setOpened(opened===item.id?"":item.id)}/>)}</div>
        {visible.length===0&&<div className="card process-empty"><HomeIcon name="files"/><h3>Nenhum processo encontrado</h3><p>Experimente outra palavra ou ajuste os filtros da busca.</p><button className="btn btn-outline-primary" type="button" onClick={clearProcessFilters}>Limpar filtros</button></div>}
        <section className="card new-process mt-4" id="novo-processo"><h2>Novo processo local</h2><form method="post" action="/central-dp/processos/salvar" className="row g-3"><input type="hidden" name="csrf" value={data.csrf||""}/><div className="col-md-6"><label className="form-label">Título<input className="form-control" name="title" maxLength="160" required/></label></div><div className="col-md-6"><label className="form-label">Categoria<input className="form-control" name="category" maxLength="80"/></label></div><div className="col-12"><label className="form-label">Conteúdo<textarea className="form-control" name="body" rows="5" required/></label></div><div className="col-12"><button className="btn btn-primary">Salvar processo</button></div></form></section>
      </>}
    </section>
  </>;
}
