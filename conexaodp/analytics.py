"""Indicadores locais calculados sobre as exportações da V2."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date


def _table(escape, headers: tuple[str, ...], rows) -> str:
    head = "".join(f"<th>{escape(value)}</th>" for value in headers)
    cells = "".join("<tr>" + "".join(f"<td>{escape(value)}</td>" for value in row) + "</tr>" for row in rows)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{cells or "<tr><td>Nenhum registro nesta seleção.</td></tr>"}</tbody></table></div>'


def _metrics(*pairs: tuple[str, int | str]) -> str:
    return '<div class="metric-grid">' + ''.join(f'<article><strong>{value}</strong><span>{label}</span></article>' for label,value in pairs) + '</div>'


def render(db, path: str, escape, can_manage: bool = False) -> str:
    rows = db.execute("SELECT registration,name,company,department,job_title,contract_type,admission_date,termination_date,termination_reason,in_active_export,in_terminated_export,in_admitted_export FROM v2_employments").fetchall()
    active = [r for r in rows if r["in_active_export"]]
    terminated = [r for r in rows if r["in_terminated_export"]]
    admitted = [r for r in rows if r["in_admitted_export"]]
    metrics = _metrics(("Ativos",len(active)),("Desligados",len(terminated)),("Admitidos no período",len(admitted)))
    if path == "/analytics/atualizar":
        logs = db.execute("SELECT source_name,row_count,imported_at FROM v2_import_log ORDER BY imported_at DESC").fetchall()
        stage = ('<section class="card"><h2>Preparar CSV da V2</h2>'
                 '<p>Use apenas o layout CSV local conhecido da V2. Esta tela não conecta ao BITTI/SIB e não altera o cadastro antes da aprovação. Arquivos incompletos não desligam pessoas ausentes.</p>'
                 '<form id="import-stage-form" class="fields"><label>Tipo de arquivo<select name="sourceName">'
                 '<option value="funcionarios_ativos.csv">Funcionários ativos</option>'
                 '<option value="funcionarios_desligados.csv">Funcionários desligados</option>'
                 '<option value="admitidos_periodo.csv">Admitidos no período</option></select></label>'
                 '<label>Empresa exata no CSV<input name="companyScope" required maxlength="160"></label>'
                 '<label>Arquivo CSV UTF-8<input name="file" type="file" accept=".csv,text/csv" required></label>'
                 '<button type="submit">Gerar prévia</button></form>'
                 '<p id="import-stage-status" role="status"></p><div id="import-stage-preview"></div>'
                 '<h3>Lotes recentes</h3><div id="import-stage-list"></div></section>'
                 '<script defer src="/static/import-staging.js"></script>') if can_manage else ''
        return (stage + '<h2>Histórico de arquivos aplicados</h2>'
                '<p>A data abaixo é a importação local; não é a data de corte da fotografia de ativos.</p>'
                + _table(escape,("Arquivo","Linhas","Importado em"),[(r["source_name"],r["row_count"],r["imported_at"]) for r in logs]))
    if path == "/analytics/qualidade":
        no_registration = sum(not r["registration"] for r in rows)
        no_admission = sum(not r["admission_date"] for r in rows)
        no_department = sum(not r["department"] for r in rows)
        no_job = sum(not r["job_title"] for r in rows)
        same_name = Counter(r["name"] for r in rows)
        repeated_names = sum(n>1 for n in same_name.values())
        return _metrics(("Vínculos",len(rows)),("Sem matrícula",no_registration),("Sem admissão",no_admission),("Sem departamento",no_department),("Sem função",no_job),("Nomes repetidos",repeated_names)) + '<p>Nomes repetidos podem representar recontratação ou homônimos; a identidade precisa de conferência.</p>'
    if path == "/analytics/admitidos-demitidos":
        months = defaultdict(lambda:[0,0])
        for r in admitted:
            if r["admission_date"]: months[r["admission_date"][:7]][0]+=1
        for r in terminated:
            if r["termination_date"]: months[r["termination_date"][:7]][1]+=1
        table_rows = [(month,a,d,a-d) for month,(a,d) in sorted(months.items(),reverse=True)]
        return metrics + _table(escape,("Mês","Admitidos","Desligados","Saldo"),table_rows)
    if path in ("/analytics/admissoes","/analytics/desligamentos"):
        subset = admitted if path.endswith("admissoes") else terminated
        field = "admission_date" if path.endswith("admissoes") else "termination_date"
        months = Counter(r[field][:7] for r in subset if r[field])
        return _metrics(("Registros",len(subset))) + _table(escape,("Mês","Registros"),sorted(months.items(),reverse=True)) + _table(escape,("Código","Nome","Empresa","Data"),[(r["registration"],r["name"],r["company"],r[field] or "—") for r in sorted(subset,key=lambda x:x[field] or "",reverse=True)[:50]])
    if path == "/analytics/retencao":
        bins = Counter()
        for r in terminated:
            if r["admission_date"] and r["termination_date"]:
                days = (date.fromisoformat(r["termination_date"])-date.fromisoformat(r["admission_date"])).days
                bins["Até 30 dias" if days<=30 else "31 a 90 dias" if days<=90 else "91 a 180 dias" if days<=180 else "181 a 365 dias" if days<=365 else "Mais de 1 ano"]+=1
        return _metrics(("Desligamentos",len(terminated))) + _table(escape,("Permanência","Vínculos"),[(k,bins[k]) for k in ("Até 30 dias","31 a 90 dias","91 a 180 dias","181 a 365 dias","Mais de 1 ano")])
    if path == "/analytics/tipos-contrato":
        counts = Counter(r["contract_type"] or "Não informado" for r in rows)
        return metrics + _table(escape,("Tipo de contrato","Vínculos"),counts.most_common())
    if path == "/analytics/perfil-ativos":
        departments = Counter(r["department"] or "Não informado" for r in active)
        jobs = Counter(r["job_title"] or "Não informado" for r in active)
        return _metrics(("Ativos",len(active)),("Departamentos",len(departments)),("Funções",len(jobs))) + '<h2>Departamentos</h2>' + _table(escape,("Departamento","Ativos"),departments.most_common(30)) + '<h2>Funções</h2>' + _table(escape,("Função","Ativos"),jobs.most_common(30))
    if path == "/analytics/contratos":
        counts = Counter(r["contract_type"] or "Não informado" for r in active)
        return _metrics(("Contratos ativos",len(active))) + _table(escape,("Tipo","Ativos"),counts.most_common())
    if path.startswith("/analytics-pro/"):
        by_company = defaultdict(lambda:[0,0,0])
        for r in rows:
            item = by_company[r["company"]]
            item[0]+=1; item[1]+=bool(r["in_active_export"]); item[2]+=bool(r["in_terminated_export"])
        table_rows = [(company,*values) for company,values in sorted(by_company.items(),key=lambda x:x[1][0],reverse=True)[:100]]
        return metrics + '<h2>Análise por empresa</h2>' + _table(escape,("Empresa","Vínculos","Ativos","Desligados"),table_rows)
    departments = Counter(r["department"] or "Não informado" for r in rows)
    return metrics + '<h2>Distribuição por departamento</h2>' + _table(escape,("Departamento","Vínculos"),departments.most_common(30))
