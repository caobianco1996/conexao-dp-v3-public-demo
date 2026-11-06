"""Incremental full-identity reconciliation when the Bridge catalog is available."""
from __future__ import annotations

import json
import time

from .integrations import IntegrationError
from .sib_import import _progress, _validate_page
from .sib_record_mapping import map_raw_employee, _code
from .sib_gap_recovery import same_identity, review


def run_page(database, client, token, cycle):
    from .database import connect
    from .sib_scan import _upsert
    from .sib_discovery import remember_gap
    url = client.config.base_url
    with connect(database) as db:
        from .sib_number_recovery import closure
        if closure(db, url):
            return
        state = db.execute('SELECT * FROM local_sib_discovery_state WHERE lease_token=?', (token,)).fetchone()
        if not state or state['catalog_next_at'] > time.time() or state['catalog_last_completed_at']:
            return
        progress = json.loads(state['catalog_progress_json']) or _progress()
        if all(item['done'] for item in progress.values()):
            return
        # Recover full employment identities first. A failure in the separate
        # company catalogue must not block duplicate registrations that are
        # keyed by employer, branch and payroll ID.
        dataset = next(key for key in ('employments', 'companies') if not progress[key]['done'])
        db.execute("UPDATE local_sib_discovery_state SET activity=?,current_registration='',current_batch_json='[]' WHERE lease_token=?",
                   (f'catalog_{dataset}', token))
    try:
        page = client.get_catalog_page(dataset, cursor=progress[dataset]['cursor'])
        rows, next_progress = _validate_page(progress[dataset], dataset, page)
        with connect(database) as db:
            db.execute('BEGIN IMMEDIATE')
            settings = db.execute('SELECT sib_base_url,sib_active FROM local_integration_settings WHERE id=1').fetchone()
            current = db.execute('SELECT enabled,running FROM local_sib_discovery_state WHERE lease_token=?', (token,)).fetchone()
            if (closure(db, url) or not current or not current['enabled'] or not current['running'] or not settings
                    or not settings['sib_active'] or settings['sib_base_url'] != url):
                return
            imported = 0
            for source_id, data_json, raw_json in rows:
                data, raw = json.loads(data_json), json.loads(raw_json)
                if dataset == 'employments':
                    payroll = str(data.get('registration') or '')
                    canonical = {'id_folha': payroll, 'name': data.get('name'), 'cpf': data.get('cpf'),
                                 'admission_date': data.get('admissionDate'), 'client_code': data.get('companyId')}
                    try:
                        item, _ = map_raw_employee({'employee': canonical, 'raw_employee': raw}, payroll)
                        if item['source_id'] != source_id:
                            raise ValueError('Chave do catálogo diverge da ficha original.')
                    except ValueError:
                        remember_gap(db, url, payroll, 'FICHA_INVALIDA')
                        review(db, cycle, payroll, 'FICHA_INVALIDA')
                        continue
                else:
                    if _code(raw.get('COD_CLI')) != source_id:
                        raise ValueError('Empresa com chave incompatível no catálogo SIB.')
                    item = {'source_id': source_id, 'data': data, 'raw': raw}
                previous = db.execute("SELECT data_json,json_extract(raw_json,'$.VIN_FUN') modality FROM local_sib_records WHERE dataset=? AND source_id=?",
                                      (dataset, source_id)).fetchone()
                stored = json.loads(previous[0]) if previous else {}
                if dataset == 'employments' and previous:
                    stored['contractTypeCode'] = stored.get('contractTypeCode') or previous['modality']
                if dataset == 'employments' and previous and not same_identity(stored, item['data']):
                    remember_gap(db, url, payroll, 'CHAVE_REUTILIZADA')
                    review(db, cycle, payroll, 'CHAVE_REUTILIZADA')
                    continue
                if dataset == 'companies' and previous:
                    item['data'] = {**json.loads(previous[0]), **item['data']}
                if not previous:
                    imported += int(dataset == 'employments')
                _upsert(db, dataset, item, cycle)
            progress[dataset] = next_progress
            done = all(item['done'] for item in progress.values())
            db.execute('''UPDATE local_sib_discovery_state SET catalog_supported=1,catalog_error='',
                catalog_progress_json=?,catalog_next_at=?,catalog_last_completed_at=CASE WHEN ? THEN ? ELSE catalog_last_completed_at END,
                imported=imported+?,lease_until=? WHERE lease_token=?''',
                (json.dumps(progress), 0, int(done), time.time(),
                 imported, time.time() + 180, token))
    except (IntegrationError, ValueError) as error:
        if isinstance(error, IntegrationError) and (error.status_code in (401, 403) or error.retryable):
            raise
        unavailable = isinstance(error, IntegrationError) and error.status_code in (404, 405, 501)
        reset = isinstance(error, IntegrationError) and error.status_code in (400, 409)
        with connect(database) as db:
            db.execute('''UPDATE local_sib_discovery_state SET catalog_supported=CASE WHEN ? THEN 0 ELSE catalog_supported END,
                catalog_error=?,catalog_next_at=?,catalog_progress_json=CASE WHEN ? THEN '{}' ELSE catalog_progress_json END,
                lease_until=? WHERE lease_token=?''',
                (int(unavailable), 'A Bridge instalada não oferece o catálogo completo de vínculos.' if unavailable else str(error)[:300],
                 time.time() + (3600 if unavailable else 60), int(reset), time.time() + 180, token))
