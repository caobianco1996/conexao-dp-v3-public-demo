"""Priority polling of new registrations, with a separate finite historical repair."""
from __future__ import annotations

import json
import logging
import time
from itertools import zip_longest

from . import integrations, sib_number_recovery as recovery
from .database import connect
from .sib_runtime import lease_active, worker_token


def _fresh_queue(service, profiles, offset):
    # Modalities sharing a physical branch also share its numeric frontier.
    branches = {}
    for item in profiles:
        if item['active'] and item.get('employer') and item.get('branch'):
            key = (item['employer'], item['branch'])
            branches[key] = max(branches.get(key, 0), item['maximum'])
    if not branches:
        branches = {('employees', ''): service._profile_highwater(profiles),
                    ('interns', ''): service._profile_highwater(profiles, interns=True)}
    windows = []
    for maximum in sorted(set(branches.values())):
        frontier = maximum + 1
        if frontier > 999999:
            continue
        future = range(frontier, min(999999, frontier + service.FRESH_IDS_PER_CYCLE - 1) + 1)
        recent = range(frontier - 1, max(0, frontier - service.LOOKBACK_IDS) - 1, -1)
        probe = frontier + service.FRESH_IDS_PER_CYCLE + offset
        farther = range(probe, min(999999, probe + min(10, service.FRESH_IDS_PER_CYCLE) - 1) + 1)
        windows.append([*future, *recent, *farther])
    return list(dict.fromkeys(str(item) for group in zip_longest(*windows)
                              for item in group if item is not None))


def _prepare(db, service, now):
    state = service._row(db)
    queue = json.loads(state['queue_json'])
    remaining = state['queue_index'] < len(queue)
    closed = recovery.closure(db, state['source_url'])
    if closed and state['queue_kind'] in ('recovery', 'catalog'):
        # Each historical cursor remains stored in its own state. Only the
        # obsolete work window is released; fresh-ID work keeps its checkpoint.
        db.execute("UPDATE local_sib_discovery_state SET queue_kind='',queue_lane='',queue_json='[]',queue_index=0 WHERE id=1")
        queue, remaining = [], False
    if recovery.priority_active(db, state['source_url']):
        repair = db.execute('SELECT retry_at FROM local_sib_number_recovery_state WHERE id=1').fetchone()
        if repair and repair['retry_at'] > now:
            return '', []
        if state['queue_kind'] == 'recovery' and state['queue_lane'] == 'interns' and remaining:
            return 'recovery', queue
        lane, queue = recovery.next_batch(db, only_lane='interns')
        if queue:
            db.execute("""UPDATE local_sib_discovery_state SET queue_kind='recovery',queue_lane=?,
                queue_json=?,queue_index=0,gap_ids_json='[]' WHERE id=1""", (lane, json.dumps(queue)))
            return 'recovery', queue
        return '', []
    if state['queue_kind'] == 'fresh' and remaining:
        return 'fresh', queue
    if state['fresh_due_at'] <= now:
        profiles = service._sequence_profiles(db)
        queue = _fresh_queue(service, profiles, int(state['probe_offset']) % service.PROBE_IDS)
        db.execute('''UPDATE local_sib_discovery_state SET queue_kind='fresh',queue_lane='',
            queue_json=?,queue_index=0,gap_ids_json='[]',cursor=?,intern_cursor=? WHERE id=1''',
            (json.dumps(queue), min(1000000, service._profile_highwater(profiles) + 1),
             min(1000000, service._profile_highwater(profiles, interns=True) + 1)))
        return 'fresh', queue
    repair = db.execute('SELECT retry_at FROM local_sib_number_recovery_state WHERE id=1').fetchone()
    progress = json.loads(state['catalog_progress_json'] or '{}')
    catalog_due = (not closed and not state['catalog_last_completed_at'] and state['catalog_next_at'] <= now
                   and (not progress or not all(item.get('done') for item in progress.values())))
    if catalog_due and recovery.catalog_turn(db):
        db.execute("UPDATE local_sib_discovery_state SET queue_kind='catalog',queue_lane='',queue_json='[]',queue_index=0 WHERE id=1")
        return 'catalog', []
    if not closed and state['queue_kind'] == 'recovery' and remaining and (not repair or repair['retry_at'] <= now):
        return 'recovery', queue
    lane, queue = recovery.next_batch(db) if not repair or repair['retry_at'] <= now else ('', [])
    if queue:
        db.execute("""UPDATE local_sib_discovery_state SET queue_kind='recovery',queue_lane=?,
            queue_json=?,queue_index=0,gap_ids_json='[]' WHERE id=1""", (lane, json.dumps(queue)))
        return 'recovery', queue
    if catalog_due:
        db.execute("UPDATE local_sib_discovery_state SET queue_kind='catalog',queue_lane='',queue_json='[]',queue_index=0 WHERE id=1")
        return 'catalog', []
    from . import sib_presence
    if sib_presence.due(db, state['source_url'], now):
        db.execute("UPDATE local_sib_discovery_state SET queue_kind='presence',queue_lane='',queue_json='[]',queue_index=0 WHERE id=1")
        return 'presence', []
    return '', []


def _finish_queue(db, service, token):
    state = service._row(db)
    if state['queue_index'] < len(json.loads(state['queue_json'])):
        return
    if state['queue_kind'] == 'fresh':
        now = time.time()
        db.execute('''UPDATE local_sib_discovery_state SET fresh_last_at=?,fresh_due_at=?,
            last_cycle_at=?,probe_offset=? WHERE lease_token=?''',
            (now, now + service.INTERVAL_SECONDS, now,
             (state['probe_offset'] + min(10, service.FRESH_IDS_PER_CYCLE)) % service.PROBE_IDS, token))
    db.execute("""UPDATE local_sib_discovery_state SET queue_json='[]',queue_index=0,
        queue_kind='',queue_lane='',gap_ids_json='[]' WHERE lease_token=?""", (token,))
    return True


def _save(db, service, token, cycle, payroll, records, problem, identified, known, tracked, *, modalities=None):
    from .sib_scan import _upsert
    from .sib_gap_recovery import same_identity, review
    from .sib_identity_hints import find_identity_hints
    new_count, confirmed_keys, observed = 0, set(), []
    for employment, company in records:
        if modalities and str(employment['data'].get('contractTypeCode')) not in modalities:
            raise ValueError('A ficha retornada não pertence à modalidade priorizada.')
        stored = db.execute("SELECT data_json,json_extract(raw_json,'$.VIN_FUN') modality FROM local_sib_records WHERE dataset='employments' AND source_id=?",
                            (employment['source_id'],)).fetchone()
        stored_data = json.loads(stored[0]) if stored else {}
        if stored:
            stored_data['contractTypeCode'] = stored_data.get('contractTypeCode') or stored['modality']
        if stored and not same_identity(stored_data, employment['data']):
            problem = 'CHAVE_REUTILIZADA'
            continue
        _upsert(db, 'employments', employment, cycle)
        if company:
            _upsert(db, 'companies', company, cycle)
        new_count += int(not stored)
        confirmed_keys.add(employment['source_id'])
        observed.append(employment['data'])
    if modalities:
        # A focused check cannot settle number-wide conflicts involving other
        # modalities. Leave those reviews for the regular recovery afterwards.
        expected = [item for item in known if str(item.get('contractTypeCode')) in modalities]
        if not confirmed_keys and expected and problem == 'AUSENCIA_CONFIRMADA':
            problem = 'FICHA_NAO_CONFIRMADA'
        if problem and problem != 'AUSENCIA_CONFIRMADA':
            url = service._row(db)['source_url']
            service.remember_gap(db, url, payroll, problem)
            service._mark_gap_retry(db, url, payroll, problem)
            review(db, cycle, payroll, problem, identified=identified)
        service._record_progress(db, token, imported=new_count,
            missing=int(problem == 'AUSENCIA_CONFIRMADA'),
            ambiguous=int(bool(problem) and problem != 'AUSENCIA_CONFIRMADA'))
        return
    expected_keys = {item['sourceId'] for item in known}
    hints = find_identity_hints(db, payroll) if records else []
    missing_hint = any(not any(item.get('cpf') == hint['cpf'] and item.get('admissionDate') == hint['admission_date']
                               for item in observed + known) for hint in hints)
    if confirmed_keys and (expected_keys - confirmed_keys or missing_hint) and not problem:
        problem = 'OUTROS_VINCULOS_PENDENTES'
    url = service._row(db)['source_url']
    if problem or not confirmed_keys:
        problem = problem or 'FICHA_NAO_CONFIRMADA'
        service.remember_gap(db, url, payroll, problem)
        service._mark_gap_retry(db, url, payroll, problem)
        review(db, cycle, payroll, problem, identified=identified)
    elif tracked:
        service._mark_gap_found(db, url, payroll)
        review(db, cycle, payroll, 'RECUPERADO_AUTOMATICAMENTE', resolved=True)
    service._record_progress(db, token, imported=new_count,
        missing=int(problem == 'AUSENCIA_CONFIRMADA'),
        ambiguous=int(bool(problem) and problem != 'AUSENCIA_CONFIRMADA'))


def run_due(database, client, *, now=None, stop_event=None, one_batch=False, fresh_only=False):
    from . import sib_discovery as service
    from .sib_gap_recovery import diagnose
    from .sib_catalog_recovery import run_page

    started = time.monotonic()
    with connect(database) as db:
        scan = db.execute('SELECT source_url,actor_id,cycle FROM local_sib_scan_state WHERE id=1').fetchone()
        if not scan or scan['source_url'] != client.config.base_url or not service._scan_done(db):
            return False
        actor_id, cycle = scan['actor_id'], scan['cycle']
    service._ensure(database, client.config.base_url, int(actor_id))
    now = time.time() if now is None else now
    with connect(database) as db:
        db.execute('BEGIN IMMEDIATE')
        state = service._row(db)
        if not state or not state['enabled'] or state['source_url'] != client.config.base_url:
            return False
        actor = db.execute("SELECT id FROM users WHERE id=? AND active=1 AND role='ADMIN'", (state['actor_id'],)).fetchone()
        if not actor:
            db.execute("UPDATE local_sib_discovery_state SET enabled=0,error='O administrador responsável está inativo ou sem permissão.' WHERE id=1")
            return False
        if (service._other_busy(db) or lease_active(state['lease_token'], state['lease_until'])
                or now < state['next_due_at'] or state['monitor_version'] < 2):
            return False
        if fresh_only and state['fresh_due_at'] > now and not (
                state['queue_kind'] == 'fresh' and state['queue_index'] < len(json.loads(state['queue_json']))):
            return False
        kind, queue = _prepare(db, service, now)
        if not kind:
            return False
        token = worker_token()
        db.execute("UPDATE local_sib_discovery_state SET running=1,lease_token=?,lease_until=?,error='' WHERE id=1",
                   (token, time.time() + service.LEASE_SECONDS))

    def renew():
        with connect(database) as db:
            current = service._row(db)
            settings = db.execute('SELECT sib_base_url,sib_active FROM local_integration_settings WHERE id=1').fetchone()
            if (not current or current['lease_token'] != token or not current['enabled'] or not current['running']
                    or service._other_busy(db) or (stop_event and stop_event.is_set())):
                raise service._YieldTurn()
            if not settings or not settings['sib_active'] or settings['sib_base_url'] != client.config.base_url:
                raise ValueError('A configuração SIB mudou durante o monitoramento.')
            if kind in ('recovery', 'catalog') and recovery.closure(db, client.config.base_url):
                raise service._YieldTurn()
            focused = recovery.priority_active(db, client.config.base_url)
            if focused and not (kind == 'recovery' and current['queue_lane'] == 'interns'):
                raise service._YieldTurn()
            if kind != 'fresh' and not focused and current['fresh_due_at'] <= time.time():
                raise service._YieldTurn()
            db.execute('UPDATE local_sib_discovery_state SET lease_until=? WHERE lease_token=?',
                       (time.time() + service.LEASE_SECONDS, token))

    error_message, raw_reads = '', 0
    try:
        renew()
        with connect(database) as db:
            focused = recovery.priority_active(db, client.config.base_url)
            if focused:
                focused_profiles = recovery.priority_profiles(db)
        if kind == 'catalog':
            run_page(database, client, token, cycle)
            return True
        if kind == 'presence':
            from . import sib_presence
            with connect(database) as db:
                db.execute("UPDATE local_sib_discovery_state SET activity='identity_inventory',current_registration='',current_batch_json='[]' WHERE lease_token=?", (token,))
            sib_presence.run_unit(database, client, renew, token)
            return True
        while not (stop_event and stop_event.is_set()):
            renew()
            with connect(database) as db:
                state = service._row(db)
                index = state['queue_index']
                if index >= len(queue):
                    _finish_queue(db, service, token)
                    return True
                batch = queue[index:index + 50]
                db.execute("UPDATE local_sib_discovery_state SET activity='scan_batch',current_registration='',current_batch_json=? WHERE lease_token=?",
                           (json.dumps(batch), token))
                placeholders = ','.join('?' for _ in batch)
                known = {}
                for item in db.execute(f"SELECT source_id,data_json FROM local_sib_records WHERE dataset='employments' AND json_extract(data_json,'$.registration') IN ({placeholders})", batch):
                    data = json.loads(item['data_json'])
                    known.setdefault(str(data.get('registration') or ''), []).append({**data, 'sourceId': item['source_id']})
                gaps = {item['payroll_id']: dict(item) for item in db.execute(f'SELECT * FROM local_sib_discovery_gaps WHERE source_url=? AND payroll_id IN ({placeholders})', [client.config.base_url, *batch])}
                lane = state['queue_lane']
                frontiers = [int(state[key]) for key in ('cursor', 'intern_cursor') if int(state[key]) > 1]
            results = {} if focused else {item['id_folha']: item for item in client.get_scan_batch(batch)}
            for payroll in batch:
                renew()
                if focused:
                    from .sib_gap_recovery import diagnose_modalities
                    with connect(database) as db:
                        db.execute("UPDATE local_sib_discovery_state SET activity='confirm_identity',current_registration=? WHERE lease_token=?", (payroll, token))
                    records, problem, identified = diagnose_modalities(client, payroll, renew=renew,
                        profiles=focused_profiles, modalities={'15'})
                    with connect(database) as db:
                        db.execute('BEGIN IMMEDIATE')
                        current = service._row(db)
                        settings = db.execute('SELECT sib_base_url,sib_active FROM local_integration_settings WHERE id=1').fetchone()
                        if (not current or current['lease_token'] != token or not settings
                                or not settings['sib_active'] or settings['sib_base_url'] != client.config.base_url):
                            return True
                        _save(db, service, token, cycle, payroll, records, problem, identified,
                              known.get(payroll, []), False, modalities={'15'})
                    if time.monotonic() - started >= service.TURN_SECONDS:
                        return True
                    continue
                response = results[payroll]
                reason = response.get('error')
                if reason not in (None, 'vinculo_ambiguo', 'identidade_nao_corresponde'):
                    raise integrations.IntegrationError('A Bridge não concluiu uma faixa do monitoramento SIB.')
                local = known.get(payroll, [])
                canonical = integrations.normalize_sib_employee(response['data'].get('employee') or {}, payroll) if response.get('data') else None
                gap = gaps.get(payroll)
                ahead = any(frontier <= int(payroll) <= frontier + service.FRESH_IDS_PER_CYCLE + service.PROBE_IDS + 10
                            for frontier in frontiers)
                if kind == 'fresh' and reason and not ahead:
                    # An old ambiguous identity in the lookback must be
                    # repaired in the finite lanes, without holding new hires.
                    with connect(database) as db:
                        service._record_progress(db, token,
                            ambiguous=int(bool(gap and gap['status'] == 'PENDING')))
                    continue
                # Historical gap evidence belongs to the finite repair, not
                # the priority fresh-ID window. Old absent numbers must not
                # cause individual full-fiche reads on every fresh poll.
                tracked = bool(reason) or bool(kind == 'recovery' and gap
                    and gap['status'] == 'PENDING'
                    and gap['last_reason'] not in ('NAO_ENCONTRADO', 'AUSENCIA_CONFIRMADA'))
                intern_missing = (kind == 'recovery' and lane == 'interns'
                                  and not any(str(item.get('contractTypeCode')) == '15' for item in local))
                # Known ambiguous lookback IDs must not monopolize fresh polling.
                cached_identity = (kind == 'fresh' and len(local) > 1 and gap
                    and (gap['next_check_at'] > time.time()
                         or (gap['status'] == 'FOUND' and time.time() - gap['last_checked_at'] < 600)))
                if cached_identity or (not tracked and not intern_missing and canonical and service._matches_known(canonical, local)):
                    with connect(database) as db:
                        service._record_progress(db, token)
                    continue
                if not tracked and not intern_missing and not canonical and not local:
                    with connect(database) as db:
                        service._record_progress(db, token, missing=1)
                    continue
                limit = service.RAW_READS_PER_TURN if kind == 'fresh' else 1
                exhausted = raw_reads >= limit or (raw_reads and time.monotonic() - started >= service.TURN_SECONDS)
                if exhausted:
                    if kind != 'fresh':
                        return True
                    with connect(database) as db:
                        service.remember_gap(db, client.config.base_url, payroll, 'NOVA_MATRICULA_PENDENTE')
                        service._record_progress(db, token, ambiguous=1)
                    continue
                raw_reads += 1
                with connect(database) as db:
                    db.execute("UPDATE local_sib_discovery_state SET activity='confirm_identity',current_registration=? WHERE lease_token=?", (payroll, token))
                records, problem, identified = diagnose(database, client, payroll, renew=renew,
                    exhaustive=(kind == 'recovery' or bool(reason)),
                    sequence_margin=(service.FRESH_IDS_PER_CYCLE + service.PROBE_IDS if kind == 'fresh' else 0))
                with connect(database) as db:
                    db.execute('BEGIN IMMEDIATE')
                    state = service._row(db)
                    settings = db.execute('SELECT sib_base_url FROM local_integration_settings WHERE id=1').fetchone()
                    if not state or state['lease_token'] != token or settings['sib_base_url'] != client.config.base_url:
                        return True
                    _save(db, service, token, cycle, payroll, records, problem, identified, local,
                          tracked or bool(gap and gap['status'] == 'PENDING'))
                if time.monotonic() - started >= service.TURN_SECONDS:
                    return True
            with connect(database) as db:
                completed = _finish_queue(db, service, token)
            if completed or one_batch:
                return True
        return True
    except service._YieldTurn:
        return True
    except Exception as error:
        if not isinstance(error, (integrations.IntegrationError, ValueError)):
            logging.getLogger(__name__).exception('Falha interna no monitor SIB')
        error_message = str(error) if isinstance(error, (integrations.IntegrationError, ValueError)) else 'A consulta foi interrompida; o ponto salvo será retomado.'
        return False
    finally:
        with connect(database) as db:
            if error_message and kind != 'fresh':
                db.execute('UPDATE local_sib_number_recovery_state SET retry_at=? WHERE id=1', (time.time() + 60,))
            db.execute("""UPDATE local_sib_discovery_state SET running=0,lease_token='',lease_until=0,error=?,
                activity='',current_registration='',current_batch_json='[]',
                next_due_at=CASE WHEN ? THEN ? ELSE next_due_at END,updated_at=CURRENT_TIMESTAMP WHERE lease_token=?""",
                (error_message[:300], int(bool(error_message) and kind == 'fresh'), time.time() + 60, token))
