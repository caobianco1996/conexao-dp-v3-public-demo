"""Organize the operational workbook without importing another employee base."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import posixpath
import re
import unicodedata
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
SOURCE_KEY = 'processos-operacionais-planilha'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS local_sector_process_enrichment (
 process_id TEXT PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES local_sector_sources(id),
 content_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_sector_message_links (
 source_id INTEGER NOT NULL REFERENCES local_sector_sources(id), source_key TEXT NOT NULL,
 content_id INTEGER NOT NULL REFERENCES local_content(id), PRIMARY KEY(source_id,source_key)
);
'''

PROCEDURES = {
 'FECHAMENTO DE FOLHA DE PGTO': 'DP-001', 'CONF DE CADASTRO': 'DP-002',
 'FGTS RESCISÓRIO': 'DP-005', 'CHAVE-FGTS E EXTRATO': 'DP-005', 'IMP. PONTO': 'DP-006',
 'AFAST. DOENÇA': 'DP-007', 'ENVIO DESTRA': 'DP-008', 'FOLHA ANALITICA': 'DP-009',
 'CADASTRO DE ESTAGIO': 'DP-010', 'PROCESSO DE ESTÁGIO': 'DP-010',
 'CADASTRO DE TERCEIRO': 'DP-011', 'CADASTRO EMPRESA': 'DP-012',
 'SOLICITAÇÃO DE PERFIL DE FOLHA': 'DP-013', 'DUPLICAR CADASTRO': 'DP-014',
 'E-SOCIAL': 'DP-015', 'SEGURO DESEMPREGO': 'DP-016', 'RDT - FGTS': 'DP-017',
 'DOC DIVERSOS': 'DP-018', 'ACOMP DE AFAST': 'DP-019', 'SOLICITAÇÃO DE PPP': 'DP-020',
 'PGTO VT': 'DP-021', 'VIA NOVA BENEFICIO': 'DP-022', 'COBR ESTÁGIO': 'DP-023',
 'AFAST. ACIDENTE E CAT': 'DP-024', 'PONTO DIARIO': 'DP-025',
 'EMITIR ESPELHO DE PONTO': 'DP-026', 'RELATÓRIO DE INCONSISTENCIA': 'DP-027',
 'FECHAMENTOS DE PONTO': 'DP-028',
}
INDEX_SHEETS = {'MENU', 'PROCESSOS', 'CONTROLES', 'TABELAS', 'CONEFERÊNCIA'}
EMPTY_SHEETS = {'FATURAMENTO TEMPTERC', 'FERIAS', 'EXAMES PERIODICOS', 'NF ESTAGIO'}
HISTORY_SHEETS = {'B. DADOS', 'AFASTADOS', 'PERÍCIA', 'PRÉ-NATAL', 'CONTROLE DE ESTÁGIO', 'CONVENIO'}
CALCULATORS = {
 'INSS E IRRF': ('INSS e IRRF', '/rotinas/tributos'),
 'H.E E AD NOT': ('Horas extras e adicional noturno', '/rotinas/calculadoras'),
 'CONF DE HORÁRIO': ('Conferência de jornada', '/rotinas/calculadoras'),
}
CREDENTIAL_ROWS = {'SEGURO DESEMPREGO': {159,160,161}, 'ACOMP DE AFAST': {60,61},
                   'ENVIO DESTRA': {3}, 'CADASTRO DE TERCEIRO': {158},
                   'IMP. PONTO': {223}, 'VIA NOVA BENEFICIO': {15}}


def normalized(value):
    text = unicodedata.normalize('NFKD', str(value or '')).casefold()
    return ' '.join(''.join(c for c in text if not unicodedata.combining(c)).split())


def row_number(ref):
    return int(re.search(r'\d+$', ref)[0])


def column_number(ref):
    number = 0
    for char in re.match('[A-Z]+', ref)[0]:
        number = number * 26 + ord(char) - 64
    return number


def _target(part, target):
    return target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(part), target))


class WorkbookSource:
    def __init__(self, path):
        self.path = Path(path)
        self.sheets, self.media, self.sheet_media, self.hyperlinks = {}, [], {}, {}
        with ZipFile(self.path) as archive:
            if sum(i.file_size for i in archive.infolist()) > 160_000_000:
                raise ValueError('A planilha excede o tamanho de leitura suportado.')
            self.media = [n for n in archive.namelist() if n.startswith('xl/media/')]
            strings = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                strings = [''.join(t.text or '' for t in item.findall('.//s:t', NS))
                           for item in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
            styles = ET.fromstring(archive.read('xl/styles.xml'))
            formats = {int(x.attrib['numFmtId']): x.attrib['formatCode'] for x in styles.findall('s:numFmts/s:numFmt', NS)}
            style_formats = [int(x.attrib.get('numFmtId', 0)) for x in styles.findall('s:cellXfs/s:xf', NS)]
            relationships = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
            for sheet in ET.fromstring(archive.read('xl/workbook.xml')).find('s:sheets', NS):
                name, part = sheet.attrib['name'], _target('xl/workbook.xml', relationships[sheet.attrib[REL]])
                root, values = ET.fromstring(archive.read(part)), {}
                for cell in root.findall('.//s:sheetData/s:row/s:c', NS):
                    node, kind = cell.find('s:v', NS), cell.attrib.get('t', '')
                    value = node.text if node is not None else None
                    if kind == 's' and value is not None:
                        value = strings[int(value)]
                    elif kind == 'inlineStr':
                        value = ''.join(t.text or '' for t in cell.findall('.//s:t', NS))
                    elif kind == 'b' and value is not None:
                        value = value == '1'
                    elif kind == 'e':
                        value = None
                    elif value is not None and kind not in ('str', 'd'):
                        try:
                            value = float(value)
                            fmt = style_formats[int(cell.attrib.get('s', 0))]
                            custom = re.sub(r'"[^"]*"|\[[^]]*\]', '', formats.get(fmt, '')).casefold()
                            date_format = fmt in (14,15,16,17,22) or ('d' in custom and ('m' in custom or 'y' in custom))
                            time_format = fmt in (18,19,20,21,22,45,46,47) or ('h' in custom and 'm' in custom)
                            if date_format:
                                stamp = datetime(1899,12,30) + timedelta(days=value)
                                value = (stamp.isoformat(timespec='seconds') if time_format else stamp.date().isoformat()) if value > 1 else None
                            elif time_format:
                                seconds = round(value * 86400)
                                value = f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}'
                            elif value.is_integer():
                                value = int(value)
                        except (ValueError, OverflowError, IndexError):
                            value = None
                    if value not in (None, ''):
                        values[cell.attrib['r']] = value
                self.sheets[name] = values
                images = []
                rel_part = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
                sheet_rels = {r.attrib['Id']: r.attrib for r in ET.fromstring(archive.read(rel_part))} if rel_part in archive.namelist() else {}
                self.hyperlinks[name] = {h.attrib['ref']: sheet_rels[h.attrib[REL]]['Target']
                    for h in root.findall('s:hyperlinks/s:hyperlink', NS)
                    if h.attrib.get(REL) in sheet_rels and sheet_rels[h.attrib[REL]].get('TargetMode') == 'External'}
                if rel_part in archive.namelist():
                    for relationship in ET.fromstring(archive.read(rel_part)):
                        if not relationship.attrib.get('Type', '').endswith('/drawing'):
                            continue
                        drawing = _target(part, relationship.attrib['Target'])
                        drawing_rels = posixpath.join(posixpath.dirname(drawing), '_rels', posixpath.basename(drawing) + '.rels')
                        if drawing_rels not in archive.namelist():
                            continue
                        for image_rel in ET.fromstring(archive.read(drawing_rels)):
                            if image_rel.attrib.get('Type', '').endswith('/image'):
                                image_part = _target(drawing, image_rel.attrib['Target'])
                                if image_part.startswith('xl/media/'):
                                    images.append(posixpath.basename(image_part))
                self.sheet_media[name] = list(dict.fromkeys(images))


def _rows(values):
    result = {}
    for ref, value in values.items():
        result.setdefault(row_number(ref), {})[re.match('[A-Z]+', ref)[0]] = value
    return result


def _safe_text(value, secrets):
    text = str(value or '').strip()
    if text in secrets:
        return '[Dado de acesso preservado no cofre]'
    for secret in sorted((v for v in secrets if len(v) >= 6), key=len, reverse=True):
        text = text.replace(secret, '[Dado de acesso no cofre]')
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[Contato na origem restrita]', text)
    text = re.sub(r'\b\d{3}\.\d{3}\.\d{3}-\d{2}\b', '[Documento na origem restrita]', text)
    text = re.sub(r'\b(?:\+?55\s*)?\(?\d{2}\)?[ -]?\d{4,5}[ -]\d{4}\b', '[Contato na origem restrita]', text)
    return text


def _destination(title):
    value = normalized(title)
    pairs = [('afast', 'Central de afastamentos', '/central-dp/afastamentos'),
             ('rescis', 'Central de rescisões', '/central-dp/rescisoes'),
             ('prorrog', 'Prorrogações', '/central-dp/prorrogacoes'),
             ('vencimento', 'Prorrogações', '/central-dp/prorrogacoes'),
             ('cadastro de empresa', 'Empresas', '/cadastros/empresas'),
             ('cadastro de funcionario', 'Pessoas', '/cadastros/pessoas'),
             ('tabelas de impostos', 'Tributos', '/rotinas/tributos'),
             ('darf', 'Tributos', '/rotinas/tributos'),
             ('ferias', 'Calculadoras DP', '/rotinas/calculadoras'),
             ('folha', 'Processos de folha', '/central-dp/processos?q=folha'),
             ('ponto', 'Processos de ponto', '/central-dp/processos?q=ponto')]
    return [{'label': label, 'href': href} for term, label, href in pairs if term in value][:1]


def _activities(book):
    cells, responsibilities = book.sheets['ATIV. SETOR'], {}
    for column in 'ABCDE':
        label = str(cells.get(column+'2') or '').strip()
        for row in range(3,15):
            task = cells.get(f'{column}{row}')
            if task and label:
                responsibilities.setdefault(normalized(task), []).append(label)
    items = []
    for row in range(24,60):
        title = str(cells.get(f'A{row}') or '').strip()
        if not title:
            continue
        payload = {'responsibleLabels': list(dict.fromkeys(responsibilities.get(normalized(title), []))),
                   'periodicity': str(cells.get(f'B{row}') or ''),
                   'complexity': str(cells.get(f'D{row}') or ''),
                   'destinationLinks': _destination(title), 'variants': [],
                   'notes': ['Responsáveis conforme a planilha. Datas de início e fim não foram informadas.']}
        items.append({'sourceKey':'activity:'+normalized(title), 'title':title,
                      'category':'Rotinas do departamento pessoal', 'sourceSheet':'ATIV. SETOR',
                      'sourceRange':f'A{row}:D{row}', 'payload':payload})
    aliases = {'FECHAMENTO DE FOLHA - DIA 05':'Folha de pagamento', 'FECHAMENTO DE FOLHA - DIA 30':'Folha de pagamento',
               'FECHAMENTO ADIANTAMENTO - DIA 15':'Folha de Adiantamento', 'FECHAMENTO ADIANTAMENTO - DIA 20':'Folha de Adiantamento',
               'VENCIMENTO DE CONTRATO':'Vencimento de Contratos', 'VENCIMENTO DE 180 DIAS':'Prorrogação de contratos',
               'ATUALIZAÇÃO DOS AFASTAMENTOS':'Afastamento'}
    extra = book.sheets['ATIVIDADES DO SETOR']
    for row in range(3,10):
        title = str(extra.get(f'A{row}') or '')
        target = next((x for x in items if normalized(x['title']) == normalized(aliases.get(title, title))), None)
        if not target:
            continue
        responsible = str(extra.get(f'E{row}') or '').strip()
        target['payload']['variants'].append({'title': title, 'responsibleLabel': responsible,
            'sourceSheet':'ATIVIDADES DO SETOR', 'sourceRange':f'A{row}:E{row}'})
        target['payload']['notes'].append(title + (' · responsável: '+responsible if responsible else ''))
    return items


def _references(book):
    values, rows, seen, descriptions = book.sheets['TABELA DE EXAMES'], [], set(), {}
    source_rows = 0
    for number, cells in sorted(_rows(values).items()):
        code, description = cells.get('A'), cells.get('B')
        if not str(code or '').isdigit() or not description:
            continue
        source_rows += 1
        pair = (str(code), ' '.join(str(description).split()).casefold())
        descriptions.setdefault(pair[0], set()).add(pair[1])
        if pair not in seen:
            rows.append([str(code), ' '.join(str(description).split())]); seen.add(pair)
    conflicts = sum(len(v)>1 for v in descriptions.values())
    yield dict(sourceKey='reference:exams',title='Catálogo de exames ocupacionais',category='Medicina ocupacional',
        sourceSheet='TABELA DE EXAMES',sourceRange='A:B',payload={'columns':['Código','Descrição'], 'rows':rows,
        'sourceRows':source_rows,'duplicatesRemoved':source_rows-len(rows),'ambiguousCodes':conflicts,
        'notes':[f'{conflicts} códigos têm descrições diferentes e foram preservados para conferência.',
                 'Referência da planilha. A tabela não cria exames nem tarefas automaticamente.']})
    for sheet, title, start, end, columns, letters, category in (
        ('FECHAMENTOS DE PONTO','Períodos de fechamento de ponto por empresa',4,10,
         ['Empresa','Dia inicial','Intervalo','Dia final'],'ABCD','Ponto'),
        ('DOC DIVERSOS','Lotes e finalidades no Bitti',101,119,['Lote','Descrição','Finalidade'],'ABG','Folha e benefícios'),
        ('DOC DIVERSOS','Benefícios por empresa na planilha',177,184,['Empresa','Benefício','Observação','Valor','Período'],
         'ABCEF','Folha e benefícios')):
        values = book.sheets[sheet]
        content = [[values.get(f'{c}{r}', '') for c in letters] for r in range(start,end+1) if values.get(f'A{r}')]
        yield dict(sourceKey='reference:'+title,title=title,category=category,sourceSheet=sheet,
            sourceRange=f'A{start}:G{end}',payload={'columns':columns,'rows':content,
            'notes':['Informação operacional da origem, sujeita a revisão. Não altera parâmetros nem perfis vigentes.'],
            'links':[{'label':'Empresas','href':'/cadastros/empresas'}, {'label':'Planejamento de lotes','href':'/rotinas/planejamento-lotes'}]})
    values = book.sheets['DOC DIVERSOS']
    yield dict(sourceKey='reference:client-paid-benefits',title='Benefícios pagos diretamente pelo cliente',category='Folha e benefícios',
        sourceSheet='DOC DIVERSOS',sourceRange='A190:C191',payload={'columns':['Empresa','Observação da origem'],
        'rows':[[values.get(f'A{r}',''),values.get(f'C{r}','')] for r in (190,191)],
        'notes':['Observação da planilha, sem alterar o perfil vigente da empresa.']})
    links = []
    for ref,url in book.hyperlinks.get('TABELAS',{}).items():
        if not re.match(r'^https?://',url,re.I):
            continue
        row = row_number(ref)
        links.append([book.sheets['TABELAS'].get(f'A{row}','Consulta da origem'),url])
    if links:
        yield dict(sourceKey='reference:source-links',title='Links de consulta da planilha',category='Consultas e tabelas',
            sourceSheet='TABELAS',sourceRange='A7:C14',payload={'columns':['Assunto','Endereço de referência'], 'rows':links,
            'notes':['Links preservados da origem. As apurações continuam nas ferramentas já existentes na V3.']})
    values = book.sheets['TIPO DE RESCISÃO']
    yield dict(sourceKey='reference:termination-types',title='Tipos de rescisão da planilha',category='Rescisões',
        sourceSheet='TIPO DE RESCISÃO',sourceRange='A3:A6',payload={'columns':['Classificação na origem'],
        'rows':[[values[f'A{r}']] for r in range(3,7) if values.get(f'A{r}')],
        'notes':['Classificações da origem. Os motivos e códigos confirmados pelo SIB continuam sendo usados nas fichas.'],
        'links':[{'label':'Rescisões','href':'/central-dp/rescisoes'}]})


def import_workbook(db, path, actor_id, *, private_root):
    from . import sector
    from .sector_history import build_histories
    actor = db.execute("SELECT id FROM users WHERE id=? AND active=1 AND role='ADMIN'", (actor_id,)).fetchone()
    if not actor:
        raise PermissionError('Somente Administrador pode organizar a planilha de processos.')
    book = WorkbookSource(path)
    raw = book.path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    source_id = sector.upsert_source(db, SOURCE_KEY, label='Planilha operacional do departamento pessoal',
        filename=book.path.name,sha256=digest,actor_id=actor_id)
    archive = Path(private_root) / (digest + '.dpapi')
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        archive.write_text(sector.protect_bytes(raw), encoding='ascii')
    secrets, credentials = set(), []
    access = book.sheets['SENHASDADOS']
    for row in [*range(6,19),*range(22,26)]:
        fields = dict(zip(('label','contact','email','login','password','identifier','url'),
                          (str(access.get(f'{c}{row}') or '').strip() for c in 'ABCDEFG')))
        fields['url'] = book.hyperlinks.get('SENHASDADOS',{}).get(f'G{row}',fields['url'])
        if not fields['login'] and not fields['password']:
            continue
        label = fields.pop('label') or f'Acesso da linha {row}'
        for key in ('login','password','identifier','email'):
            if fields[key]:secrets.add(fields[key])
        credentials.append((f'access:{row}', label, fields,'SENHASDADOS',f'A{row}:G{row}'))
    for sheet, numbers in CREDENTIAL_ROWS.items():
        contents = [{ 'cell':ref, 'value':value} for ref,value in book.sheets[sheet].items() if row_number(ref) in numbers]
        if contents:
            credentials.append(('procedure-access:'+sheet,'Acessos da orientação: '+sheet,
                {'notes':'\n'.join(f"{c['cell']}: {c['value']}" for c in contents)},sheet,', '.join(str(r) for r in sorted(numbers))))
            for cell in contents:
                if len(str(cell['value'])) <= 120:secrets.add(str(cell['value']))
    for key,label,payload,sheet,ref in credentials:
        sector.upsert_credential(db,source_id,key,label=label,payload=payload,source_sheet=sheet,source_range=ref,actor_id=actor_id)
    for item in _activities(book):
        sector.upsert_item(db,source_id,item.pop('sourceKey'),kind='ACTIVITY',title=item.pop('title'),
            category=item.pop('category'),source_sheet=item.pop('sourceSheet'),source_range=item.pop('sourceRange'),
            payload=item['payload'],actor_id=actor_id)
    reference_count = 0
    for item in _references(book):
        sector.upsert_item(db,source_id,item['sourceKey'],kind='REFERENCE',title=item['title'],category=item['category'],
            source_sheet=item['sourceSheet'],source_range=item['sourceRange'],payload=item['payload'],actor_id=actor_id)
        reference_count += 1
    history = build_histories(book.sheets, db)
    for item in history['items']:
        sector.upsert_item(db,source_id,item['sourceKey'],kind='HISTORY',title=item['title'],category=item['category'],
            source_sheet=item['sourceSheet'],source_range=item['sourceRange'],payload=item['payload'],sensitive=True,
            person_key=item.get('personKey',''),actor_id=actor_id)
    enrichments = {}
    for sheet, code in PROCEDURES.items():
        process_id = 'conexao-dp-primary:procedure:' + code
        if not db.execute("SELECT 1 FROM v2_source_rows WHERE table_name='procedures' AND source_id=?", (process_id,)).fetchone():
            continue
        lines = []
        for ref,value in sorted(book.sheets[sheet].items(), key=lambda x:(row_number(x[0]),column_number(x[0]))):
            number = row_number(ref)
            if number in CREDENTIAL_ROWS.get(sheet, set()) or isinstance(value,(int,float)):
                continue
            if sheet == 'DOC DIVERSOS' and 98 <= number <= 119 or sheet == 'DOC DIVERSOS' and 173 <= number <= 191:
                continue
            text = _safe_text(value,secrets)
            if not text or normalized(text) in ('menu','ir') or 'voltar ao menu' in normalized(text):
                continue
            lines.append({'cell':ref,'text':text})
        if not lines:
            continue
        section = {'title':sheet,'sourceSheet':sheet,'sourceRange':f"{lines[0]['cell']}:{lines[-1]['cell']}",
                   'lines':lines,'images':[{'name':name,'href':f'/api/ui/sector/media/{source_id}/{name}'}
                       for name in book.sheet_media[sheet] if re.fullmatch(r'[A-Za-z0-9_.-]+\.(?:png|jpe?g|gif|webp)',name,re.I)],
                   'notes':['Orientação da planilha original; revise a vigência antes de aplicar. Acessos foram separados para a área restrita.']}
        enrichments.setdefault(process_id,[]).append(section)
    for process_id,sections in enrichments.items():
        content = {'sections':sections,'sourceId':source_id,'sourceFilename':book.path.name}
        db.execute('''INSERT INTO local_sector_process_enrichment(process_id,source_id,content_json) VALUES(?,?,?)
            ON CONFLICT(process_id) DO UPDATE SET source_id=excluded.source_id,content_json=excluded.content_json,updated_at=CURRENT_TIMESTAMP''',
            (process_id,source_id,json.dumps(content,ensure_ascii=False)))
    message = _safe_text(book.sheets['SOLICITAÇÃO DE PERFIL DE FOLHA'].get('B50',''), secrets)
    if message:
        link = db.execute('SELECT content_id FROM local_sector_message_links WHERE source_id=? AND source_key=?', (source_id,'payroll-profile')).fetchone()
        if not link:
            row = db.execute("SELECT id FROM local_content WHERE kind='MENSAGEM' AND title=?", ('Solicitação de perfil de folha',)).fetchone()
            content_id = row[0] if row else db.execute('INSERT INTO local_content(kind,title,category,body,created_by) VALUES(?,?,?,?,?)',
                ('MENSAGEM','Solicitação de perfil de folha','Folha de pagamento',message,actor_id)).lastrowid
            db.execute('INSERT INTO local_sector_message_links VALUES(?,?,?)', (source_id,'payroll-profile',content_id))
    mapping = []
    for sheet in book.sheets:
        if sheet in INDEX_SHEETS:dest,status='Navegação existente','Índice reutilizado'
        elif sheet in EMPTY_SHEETS:dest,status='Sem conteúdo para importar','Aba vazia ignorada'
        elif sheet in CALCULATORS:dest,status=CALCULATORS[sheet][0],'Ferramenta existente reutilizada'
        elif sheet in HISTORY_SHEETS:dest,status='Histórico restrito / prontuário','Histórico preservado; situação atual do SIB mantida'
        elif sheet in ('ATIV. SETOR','ATIVIDADES DO SETOR'):dest,status='Organização do setor · Atividades','Matriz consolidada'
        elif sheet == 'SENHASDADOS':dest,status='Organização do setor · Acessos','Acessos protegidos'
        elif sheet == 'TABELA DE EXAMES' or sheet == 'TIPO DE RESCISÃO':dest,status='Organização do setor · Referências','Referência organizada'
        elif sheet in PROCEDURES:dest,status='Processos existentes · '+PROCEDURES[sheet],'Conteúdo complementado'
        else:dest,status='Origem restrita','Conservada para revisão'
        mapping.append({'sheet':sheet,'destination':dest,'status':status})
    exam_reference = next(_references(book))['payload']
    metadata = {'sheetCount':len(book.sheets),'emptySheets':sorted(EMPTY_SHEETS),'emptySheetCount':len(EMPTY_SHEETS),
                'calculatorDuplicatesSkipped':len(CALCULATORS),'examDuplicatesRemoved':exam_reference['duplicatesRemoved'],
                'examAmbiguousCodes':exam_reference['ambiguousCodes'],
                'sheetMapping':mapping,'activities':len(_activities(book)),'references':reference_count,
                'history':history['summary'],'processesEnriched':len(enrichments),'credentials':len(credentials),
                'mediaCount':len(book.media),'encryptedSourcePath':str(archive.resolve()),
                'calculatorLinks':[{'label':label,'href':href} for label,href in CALCULATORS.values()],
                'notes':['INSS e IRRF da planilha referem-se a 2024 e não substituem as tabelas atuais.',
                         'As imagens originais podem conter dados pessoais e são restritas ao administrador.']}
    sector.upsert_source(db,SOURCE_KEY,label='Planilha operacional do departamento pessoal',filename=book.path.name,
                        sha256=digest,metadata=metadata,actor_id=actor_id)
    return {'sourceId':source_id,**{k:v for k,v in metadata.items() if k!='encryptedSourcePath'}}
