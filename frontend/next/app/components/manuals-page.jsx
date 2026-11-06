"use client";

import { useEffect, useRef, useState } from "react";

const labelDate = value => value ? new Date(`${String(value).slice(0,10)}T12:00:00`).toLocaleDateString("pt-BR") : "";
async function parseManualResponse(response, fallback) {
  const type = response.headers.get("content-type")?.toLowerCase() || "";
  const redirectedToLogin = response.redirected || /(^|\/)entrar(?:[?#]|$)/i.test(response.url || "");
  if (redirectedToLogin) throw new Error("Sua sessão expirou. Entre novamente.");
  if (!type.includes("application/json")) {
    if (response.status === 401 || response.status === 403) throw new Error("Sua sessão expirou. Entre novamente.");
    throw new Error(response.ok ? "O servidor respondeu em um formato inesperado. Tente novamente." : `${fallback} (HTTP ${response.status}).`);
  }
  const payload = await response.json().catch(() => null);
  if (!payload || typeof payload !== "object") throw new Error("O servidor enviou uma resposta inválida. Tente novamente.");
  if (!response.ok) throw new Error(payload.error || `${fallback} (HTTP ${response.status}).`);
  return payload;
}

function ManualBody({ manual }) {
  const body = manual.content;
  if (typeof body === "string" && body.trim()) {
    return <><section className="manual-content-section"><h3>Conteúdo do documento</h3><div className="manual-source-text">{body}</div></section>{Array.isArray(manual.images)&&manual.images.length>0&&<section className="manual-content-section"><h3>Imagens do material</h3><div className="manual-images">{manual.images.map((image,index)=><figure key={`${image.src||"image"}-${index}`}><img src={`/manual-assets${image.src}`} alt={image.alt||image.caption||`Imagem da página ${image.page||index+1}`} loading="lazy"/><figcaption>{image.caption||`Página ${image.page||index+1}`}</figcaption></figure>)}</div></section>}</>;
  }
  if (Array.isArray(body) && body.length) return <div className="manual-structured-content">{body.map((section,index)=><section className="manual-content-section" key={index}><h3>{section.title||`Seção ${index+1}`}</h3><p>{section.detail||section.content||""}</p></section>)}</div>;
  const nested = body && typeof body === "object" ? body : {};
  const steps = Array.isArray(manual.steps) && manual.steps.length ? manual.steps : (manual.quickGuide || nested.quickGuide || []);
  return <div className="manual-structured-content">
    {manual.whenToUse && <section className="manual-content-section"><h3>Quando usar</h3><p>{manual.whenToUse}</p></section>}
    {manual.quickPath && <section className="manual-content-section"><h3>Caminho</h3><p className="manual-path">{manual.quickPath}</p></section>}
    {steps.length > 0 && <section className="manual-content-section"><h3>Passo a passo</h3><ol>{steps.map((step,index)=><li key={index}>{typeof step === "string" ? step : <><strong>{step.title || `Etapa ${index+1}`}</strong>{step.detail && <span>{step.detail}</span>}</>}</li>)}</ol></section>}
    {(manual.prerequisites || []).length > 0 && <section className="manual-content-section"><h3>Antes de começar</h3><ul>{manual.prerequisites.map((item,index)=><li key={index}>{item}</li>)}</ul></section>}
    {(manual.alerts || []).length > 0 && <section className="manual-content-section"><h3>Atenção</h3><ul>{manual.alerts.map((item,index)=><li key={index}>{item}</li>)}</ul></section>}
    {Object.keys(nested).filter(key=>key!="quickGuide"&&key!="steps").length>0 && <section className="manual-content-section"><h3>Conteúdo</h3><pre className="manual-source-text">{JSON.stringify(nested,null,2)}</pre></section>}
    {!steps.length && !manual.whenToUse && !manual.quickPath && !Object.keys(nested).length && <p>O conteúdo completo não está disponível nesta cópia local. A fonte cadastrada permanece identificada acima.</p>}
  </div>;
}

export default function ManualsPage() {
  const [query,setQuery]=useState(""),[system,setSystem]=useState(""),[category,setCategory]=useState(""),[status,setStatus]=useState("");
  const [page,setPage]=useState(1),[retry,setRetry]=useState(0),[data,setData]=useState(null),[busy,setBusy]=useState(true),[error,setError]=useState(""),[opened,setOpened]=useState(null),[detailBusy,setDetailBusy]=useState(false),[detailError,setDetailError]=useState("");
  const [debounced,setDebounced]=useState("");const requestNo=useRef(0),returnScroll=useRef(0);
  useEffect(()=>{const timer=setTimeout(()=>{setDebounced(query.trim());setPage(1);},240);return()=>clearTimeout(timer);},[query]);
  useEffect(()=>{
    const controller=new AbortController();const request=++requestNo.current;
    setBusy(true);setError("");
    const params=new URLSearchParams({q:debounced,system,category,status,page:String(page)});
    fetch(`/api/ui/manuals?${params}`,{credentials:"same-origin",cache:"no-store",signal:controller.signal})
      .then(response=>parseManualResponse(response,"Não foi possível carregar os manuais."))
      .then(payload=>{if(request===requestNo.current)setData(payload);})
      .catch(reason=>{if(reason.name!=="AbortError"&&request===requestNo.current)setError(reason.message);})
      .finally(()=>{if(request===requestNo.current)setBusy(false);});
    return()=>controller.abort();
  },[debounced,system,category,status,page,retry]);

  async function openManual(id){
    returnScroll.current=window.scrollY;
    setOpened(null);setDetailBusy(true);setDetailError("");
    try{const response=await fetch(`/api/ui/manuals/${encodeURIComponent(id)}`,{credentials:"same-origin",cache:"no-store"});const payload=await parseManualResponse(response,"Não foi possível abrir este manual.");setOpened(payload.manual);}
    catch(reason){setDetailError(reason.message);}finally{setDetailBusy(false);}
  }
  function backToResults(){setOpened(null);setDetailError("");requestAnimationFrame(()=>window.scrollTo({top:returnScroll.current,behavior:"auto"}));}
  const clearFilters=()=>{setQuery("");setSystem("");setCategory("");setStatus("");setPage(1);};
  const sourceText=item=>item.sourceLocator||item.sourceFilename||"Fonte não informada";

  return <>
    
    <section className="container-fluid page-content manuals-page">
      <div className="manuals-head"><div><p className="text-secondary">Encontre orientações completas por sistema ou categoria.</p><small>Consulta somente leitura · Conteúdo e referências conforme as fontes cadastradas.</small></div></div>
      {opened ? <article className="card manual-detail">
        <div className="manual-detail-head"><div><button className="btn btn-outline-primary btn-sm" onClick={backToResults}>← Voltar aos resultados</button><div className="manual-tags"><span>{opened.system}</span><span>{opened.category}</span><span className={`manual-status ${opened.status==="DRAFT"?"is-draft":""}`}>{opened.status==="DRAFT"?"Rascunho":"Publicado"}</span></div><h2>{opened.title}</h2>{opened.description&&<p>{opened.description}</p>}</div></div>
        <div className="manual-metadata"><div><small>Fonte</small><strong>{opened.sourceLocator||opened.sourceFilename||"Não informada"}</strong></div>{opened.version&&<div><small>Versão</small><strong>{opened.version}</strong></div>}{opened.validFrom&&<div><small>Vigência</small><strong>{labelDate(opened.validFrom)||opened.validFrom}</strong></div>}{opened.updatedAt&&<div><small>Revisado em</small><strong>{labelDate(opened.updatedAt)||opened.updatedAt}</strong></div>}{opened.pageCount&&<div><small>Páginas</small><strong>{opened.pageCount}</strong></div>}</div>
        <ManualBody manual={opened}/>
        {Array.isArray(opened.assets)&&opened.assets.some(asset=>asset.publicationStatus==="REFERENCE_ONLY")&&<p className="manual-source-note">A fonte registra imagens de referência, mas os arquivos visuais dessas páginas não estão disponíveis nesta instalação.</p>}
      </article> : <>
        <nav className="manual-collections" aria-label="Coleções de manuais"><button className={!system?"is-active":""} onClick={()=>{setSystem("");setPage(1);}}>Todos os manuais</button>{(data?.systems||["RHiD","Secullum"]).map(item=><button key={item} className={system===item?"is-active":""} onClick={()=>{setSystem(system===item?"":item);setPage(1);}}>{item}</button>)}</nav>
        <div className="card manuals-toolbar"><label className="manual-search">Pesquisar<input className="form-control" type="search" value={query} onChange={event=>setQuery(event.target.value)} placeholder="Título, conteúdo, fonte ou palavra-chave" aria-label="Pesquisar manuais"/></label>
          <label>Categoria<select className="form-select" value={category} onChange={event=>{setCategory(event.target.value);setPage(1);}}><option value="">Todas</option>{(data?.categories||[]).map(item=><option key={item} value={item}>{item}</option>)}</select></label>
          {data?.canSeeDrafts&&<label>Situação<select className="form-select" value={status} onChange={event=>{setStatus(event.target.value);setPage(1);}}><option value="">Todas</option><option value="PUBLISHED">Publicados</option><option value="DRAFT">Rascunhos</option></select></label>}
          {(query||system||category||status)&&<button className="btn btn-outline-secondary btn-sm manual-clear" onClick={clearFilters}>Limpar filtros</button>}
        </div>
        <div className="manual-list-summary"><span>{busy?"Carregando lista…":`${data?.total||0} manual(is) encontrado(s)`}</span>{data&&!busy&&<span>Ordenação: título A–Z · {data.pageSize} por página</span>}</div>
        {error&&<div className="alert alert-warning" role="alert">{error}{error.toLocaleLowerCase("pt-BR").includes("sessão")&&<> <a href="/entrar">Entrar novamente</a></>} <button className="btn btn-link btn-sm" onClick={()=>setRetry(value=>value+1)}>Tentar novamente</button></div>}
        {detailError&&<div className="alert alert-warning" role="alert">{detailError}</div>}
        {detailBusy&&<div className="card manuals-empty" role="status">Carregando conteúdo do manual…</div>}
        {!busy&&!error&&data&&<>
          {data.items.length? <div className="manual-list" aria-label="Resultados de manuais">{data.items.map(item=><article className="card manual-row" key={item.id}><div className="manual-row-main"><div className="manual-row-titleline"><h2>{item.title}</h2>{item.status==="DRAFT"&&<span className="manual-status is-draft">Rascunho</span>}</div><div className="manual-row-meta"><span>{item.system}</span><span>{item.category}</span><span>{sourceText(item)}{item.pageStart?` · p. ${item.pageStart}${item.pageEnd&&item.pageEnd!==item.pageStart?`–${item.pageEnd}`:""}`:""}</span></div></div><button className="btn btn-outline-primary btn-sm" onClick={()=>openManual(item.id)}>Abrir</button></article>)}</div> : <div className="card manuals-empty">{query||category||system?"Não encontramos manuais com esses filtros.":"Nenhum manual disponível para esta conta."}</div>}
          {data.pages>1&&<nav className="manual-pagination" aria-label="Paginação dos manuais"><button className="btn btn-outline-primary btn-sm" disabled={page<=1} onClick={()=>setPage(p=>Math.max(1,p-1))}>Anterior</button><span>Página {data.page} de {data.pages}</span><button className="btn btn-outline-primary btn-sm" disabled={page>=data.pages} onClick={()=>setPage(p=>Math.min(data.pages,p+1))}>Próxima</button></nav>}
          {data.excludedProcessRecords>0&&<p className="manual-source-note">3 registros da fonte já estão cadastrados em Processos e não são repetidos aqui.</p>}
        </>}
      </>}
    </section>
  </>;
}
