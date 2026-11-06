import React from 'react';
import { createRoot } from 'react-dom/client';
import 'bootstrap/dist/css/bootstrap.min.css';
import './style.css';

function Field({ label, children, className = '' }) {
  return <div className={`mb-3 ${className}`}><label className="form-label">{label}{children}</label></div>;
}

function CaseCreateForm({ config }) {
  const [dueDate, setDueDate] = React.useState('');
  const [renew, setRenew] = React.useState(false);
  const [checklist, setChecklist] = React.useState('');

  return <section className="card case-create-react" id="novo">
    <h2 className="h5 mb-1">Novo caso</h2>
    <p className="text-secondary mb-3">Demanda geral ou vinculada a um cliente. Itens iniciados por * serão obrigatórios.</p>
    <form method="post" action="/central-dp/casos" className="row g-3">
      <input type="hidden" name="csrf" value={config.csrf}/>
      <input type="hidden" name="request_id" value={config.requestId}/>
      <Field label="Tipo" className="col-md-4"><select className="form-select" name="kind" defaultValue="GERAL">{config.kinds.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></Field>
      <Field label="Título" className="col-md-8"><input className="form-control" name="title" maxLength="160" required/></Field>
      <Field label="Cliente" className="col-md-6"><select className="form-select" name="client_id"><option value="">Geral / interno</option>{config.clients.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></Field>
      <Field label="Unidade" className="col-md-6"><select className="form-select" name="unit_id"><option value="">Sem unidade</option>{config.units.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></Field>
      <Field label="Responsável" className="col-md-4"><select className="form-select" name="owner_id"><option value="">Sem responsável</option>{config.users.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></Field>
      <Field label="Prioridade" className="col-md-4"><select className="form-select" name="priority" defaultValue="NORMAL"><option value="LOW">Baixa</option><option value="NORMAL">Normal</option><option value="HIGH">Alta</option><option value="URGENT">Urgente</option></select></Field>
      <Field label="Prazo" className="col-md-4"><input className="form-control" name="due_date" type="date" value={dueDate} onChange={event => setDueDate(event.target.value)}/></Field>
      <div className="col-12"><div className="form-check"><input className="form-check-input" id="case-renew-monthly" type="checkbox" name="renew_monthly" value="1" checked={renew} onChange={event => setRenew(event.target.checked)}/><label className="form-check-label" htmlFor="case-renew-monthly">Renovar mensalmente ao concluir (exige prazo)</label></div>{renew && !dueDate && <div className="form-text text-danger">Informe um prazo para configurar a renovação mensal.</div>}</div>
      <Field label="Descrição" className="col-12"><textarea className="form-control" name="description" rows="3" maxLength="4000"/></Field>
      <Field label="Checklist inicial" className="col-12"><textarea className="form-control" name="checklist" rows="4" maxLength="6000" placeholder="Uma tarefa por linha; use * no início para marcar como obrigatória" value={checklist} onChange={event => setChecklist(event.target.value)}/></Field>
      <div className="col-12"><button className="btn btn-primary" disabled={renew && !dueDate}>Criar caso</button></div>
    </form>
  </section>;
}

const mount = document.getElementById('case-create-root');
if (mount) {
  try {
    createRoot(mount).render(<CaseCreateForm config={JSON.parse(mount.dataset.config)}/>);
  } catch (error) {
    mount.textContent = 'Não foi possível carregar o formulário de criação. Atualize a página.';
    console.error('Falha ao iniciar a interface React de casos.', error);
  }
}
