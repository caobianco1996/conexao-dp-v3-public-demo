"""Read-only diagnosis of gaps; a numeric match never settles another identity."""
from __future__ import annotations

import json

from .integrations import IntegrationError
from .sib_record_mapping import _code, map_raw_employee


def same_identity(stored, current):
    """Do not replace an employment or its dossier after a reused source key."""
    return all(not stored.get(key) or not current.get(key) or str(stored[key]) == str(current[key])
               for key in ('cpf', 'admissionDate', 'contractTypeCode'))


def _recovery_profiles(db):
    """Use confirmed physical sequences; an internship number is not a branch."""
    # Aggregate inside SQLite rather than parsing every complete employee fiche
    # in Python for each numeric recovery visit.
    rows = db.execute("""WITH identities AS (SELECT source_id,
        json_extract(data_json,'$.registration') data_registration,
        json_extract(raw_json,'$.EMP_FUN') employer,
        json_extract(raw_json,'$.FIL_FUN') branch,
        json_extract(raw_json,'$.COD_FUN') registration,
        json_extract(raw_json,'$.VIN_FUN') modality
        FROM local_sib_records WHERE dataset='employments')
        SELECT CAST(employer AS INTEGER) employer,CAST(branch AS INTEGER) branch,
        CAST(modality AS INTEGER) modality,MAX(CAST(registration AS INTEGER)) maximum
        FROM identities
        WHERE COALESCE(CAST(employer AS TEXT),'')<>'' AND CAST(employer AS TEXT) NOT GLOB '*[^0-9]*'
        AND COALESCE(CAST(branch AS TEXT),'')<>'' AND CAST(branch AS TEXT) NOT GLOB '*[^0-9]*'
        AND COALESCE(CAST(registration AS TEXT),'')<>'' AND CAST(registration AS TEXT) NOT GLOB '*[^0-9]*'
        AND COALESCE(CAST(modality AS TEXT),'')<>'' AND CAST(modality AS TEXT) NOT GLOB '*[^0-9]*'
        AND CAST(modality AS INTEGER) IN (1,4,15)
        AND source_id=CAST(employer AS INTEGER)||':'||CAST(branch AS INTEGER)||':'||CAST(registration AS INTEGER)
        AND CAST(data_registration AS TEXT)=CAST(CAST(registration AS INTEGER) AS TEXT)
        GROUP BY CAST(employer AS INTEGER),CAST(branch AS INTEGER),CAST(modality AS INTEGER)""")
    return {(str(row['employer']), str(row['branch']), str(row['modality'])):
            {'maximum': int(row['maximum'])} for row in rows}


def _selected_record(payload, payroll, modality, employer='', branch=''):
    """Verify selectors here as well, so ignored Bridge filters remain pending."""
    employment, company = map_raw_employee(payload, payroll)
    raw = employment['raw']
    if (_code(raw.get('VIN_FUN')) != modality
            or (employer and _code(raw.get('EMP_FUN')) != employer)
            or (branch and _code(raw.get('FIL_FUN')) != branch)):
        raise ValueError('A Bridge não confirmou a empresa, filial e modalidade solicitadas.')
    return employment, company


def diagnose_modalities(client, payroll, *, renew, profiles, modalities):
    """Read only the confirmed physical sequences for the requested modalities."""
    selections = [(employer, branch, modality) for (employer, branch, modality), profile in profiles.items()
                  if modality in modalities and int(payroll) <= profile['maximum']]
    if not selections:
        raise ValueError('Não há filial confirmada para a modalidade de vínculo solicitada.')
    records, incomplete = {}, False
    for employer, branch, modality in selections:
        renew()
        try:
            payload = client.get_employee_raw(payroll, contract_type_code=modality,
                employer_code=employer, branch_code=branch)
            item = _selected_record(payload, payroll, modality, employer, branch)
            records[item[0]['source_id']] = item
        except ValueError:
            incomplete = True
        except IntegrationError as error:
            if error.status_code == 404:
                continue
            if error.status_code not in (409, 500):
                raise
            incomplete = True
    reason = ('OUTROS_VINCULOS_PENDENTES' if records else 'IDENTIDADES_NAO_CONFIRMADAS') if incomplete else ('' if records else 'AUSENCIA_CONFIRMADA')
    return list(records.values()), reason, False


def diagnose(database, client, payroll, *, renew, exhaustive=False, contract_type_codes=None,
             sequence_margin=0):
    """Return full records or a precise pending reason, without fabricating rows."""
    from .database import connect
    from .sib_identity_hints import find_identity_hints

    requested_modalities = {'1', '4', '15'}
    if exhaustive and contract_type_codes is not None:
        requested_modalities = {_code(value) for value in contract_type_codes}
        if not requested_modalities or not requested_modalities <= {'1', '4', '15'}:
            raise ValueError('Informe as modalidades SIB confirmadas: 1, 4 ou 15.')
    records, direct_status, incomplete = [], None, False
    renew()
    try:
        payload = client.get_employee_raw(payroll)
        employment, company = map_raw_employee(payload, payroll)
        if not exhaustive:
            return [(employment, company)], '', False
        if _code(employment['raw'].get('VIN_FUN')) in requested_modalities:
            records.append((employment, company))
    except ValueError:
        if not exhaustive:
            return [], 'FICHA_INVALIDA', False
        incomplete = True
    except IntegrationError as error:
        if error.status_code not in (404, 409, 500):
            raise
        direct_status = error.status_code

    with connect(database) as db:
        hints = find_identity_hints(db, payroll)
        known = [json.loads(row[0]) for row in db.execute("""SELECT data_json FROM local_sib_records
            WHERE dataset='employments' AND json_extract(data_json,'$.registration')=?""", (payroll,))]
        capability = db.execute('SELECT catalog_supported FROM local_sib_discovery_state WHERE id=1').fetchone()
        modalities = {str(row[0]) for row in db.execute('SELECT code FROM local_sib_contract_types') if str(row[0]).isdigit()}
        modalities.update(str(row[0]) for row in db.execute("""SELECT json_extract(raw_json,'$.VIN_FUN')
            FROM local_sib_records WHERE dataset='employments' AND json_extract(data_json,'$.registration')=?""",
            (payroll,)) if row[0] is not None and str(row[0]).isdigit())
        profiles = _recovery_profiles(db) if exhaustive or (capability and capability[0] == 1) else {}
    if exhaustive or (capability and capability[0] == 1):
        # The extension accepts VIN_FUN and returns the complete selected row.
        # Each response is checked by the client, including when an older API
        # silently ignores this optional parameter.
        selections = [(modality, employer, branch)
                      for (employer, branch, modality), profile in sorted(profiles.items())
                      if int(payroll) <= profile['maximum'] + max(0, int(sequence_margin))
                      and (not exhaustive or modality in requested_modalities)]
        if not exhaustive:
            # Older ambiguity recovery also checks known physical branches.
            # Retain unqualified modality probes for previously unseen sequences.
            selections.extend((modality, '', '') for modality in sorted(modalities, key=int))
        by_key = {item[0]['source_id']: item for item in records}
        for modality, employer, branch in dict.fromkeys(selections):
            renew()
            try:
                selectors = {'contract_type_code': modality}
                if employer:
                    selectors.update(employer_code=employer, branch_code=branch)
                selected = client.get_employee_raw(payroll, **selectors)
                record = _selected_record(selected, payroll, modality, employer, branch)
                previous = by_key.get(record[0]['source_id'])
                if previous and not same_identity(previous[0]['data'], record[0]['data']):
                    incomplete = True
                    continue
                by_key[record[0]['source_id']] = record
            except ValueError:
                incomplete = True
            except IntegrationError as error:
                if error.status_code == 404:
                    continue
                if error.status_code not in (409, 500):
                    raise
                incomplete = True
        records = list(by_key.values())
        if records:
            return records, 'OUTROS_VINCULOS_PENDENTES' if incomplete else '', False
    # Check the individual batch, too: older Bridges label even an empty ID as
    # ambiguous in grouped reads. A transport failure is never proof of absence.
    renew()
    single = client.get_scan_batch([payroll])[0]
    if single.get('error') not in (None, 'vinculo_ambiguo', 'identidade_nao_corresponde'):
        raise IntegrationError('A Bridge não concluiu a reconsulta individual da lacuna.')
    if direct_status == 404 and single.get('data') is None and not single.get('error') and not known and not hints and not incomplete:
        return [], 'AUSENCIA_CONFIRMADA', False

    # These hints confirm identity only. Bridge 6.6 does not return raw fields in
    # this response; a canonical match must never be presented as a saved fiche.
    identified = False
    if hints:
        with connect(database) as db:
            gap = db.execute("SELECT attempts FROM local_sib_discovery_gaps WHERE source_url=? AND payroll_id=?",
                             (client.config.base_url, payroll)).fetchone()
        offset = (int(gap[0]) if gap else 0) % len(hints)
        hint = hints[offset]
        renew()
        try:
            confirmed = client.identify_employee(payroll, cpf=hint['cpf'],
                admission_date=hint['admission_date'], taker_cnpj=hint['taker_cnpj'])
            identified = True
            if isinstance(confirmed.get('raw_employee'), dict):
                employment, company = map_raw_employee(confirmed, payroll)
                if not exhaustive or _code(employment['raw'].get('VIN_FUN')) in requested_modalities:
                    return [(employment, company)], '', True
        except ValueError:
            return [], 'FICHA_INVALIDA', False
        except IntegrationError as error:
            if error.retryable or error.status_code in (401, 403, 429, 502, 503, 504):
                raise
    return [], ('FICHA_COMPLETA_INDISPONIVEL' if identified else
                'IDENTIDADES_NAO_CONFIRMADAS' if known or len(hints) > 1 else
                single.get('error') or 'FICHA_NAO_CONFIRMADA'), identified


def review(db, cycle, payroll, reason, *, identified=False, resolved=False):
    """Expose automatic recovery in the existing conflict screen."""
    messages = {
        'AUSENCIA_CONFIRMADA': 'A consulta individual não encontrou ficha. O número continuará sendo revisitado.',
        'FICHA_INVALIDA': 'A Bridge retornou uma ficha incompleta. Nenhuma identidade foi inventada.',
        'FICHA_COMPLETA_INDISPONIVEL': 'Identidade confirmada; falta a ficha completa na Bridge para importar.',
        'IDENTIDADES_NAO_CONFIRMADAS': 'Há matrícula salva ou pistas de outra identidade; isso não confirma todos os vínculos.',
        'CHAVE_REUTILIZADA': 'Matrícula, vínculo ou identidade divergem da ficha salva. O cadastro existente foi preservado.',
        'OUTROS_VINCULOS_PENDENTES': 'Uma ficha foi confirmada, mas ainda há outro vínculo ou identidade sem confirmação.',
        'RECUPERADO_AUTOMATICAMENTE': 'Ficha completa recuperada e salva após reconsulta automática.',
    }
    state = ('RESOLVED' if resolved else 'IDENTIFIED' if identified else
             'NOT_FOUND' if reason == 'AUSENCIA_CONFIRMADA' else 'NEEDS_SOURCE')
    evidence = json.dumps({'origin': 'Bridge · recuperação automática', 'code': reason,
                          'message': messages.get(reason, 'A Bridge ainda não individualizou matrícula e vínculo.'),
                          'duplicateConfirmed': False}, ensure_ascii=False, separators=(',', ':'))
    db.execute("""INSERT INTO local_sib_issue_reviews(cycle,payroll_id,status,reason_code,evidence_json,attempts)
        VALUES(?,?,?,?,?,1) ON CONFLICT(cycle,payroll_id) DO UPDATE SET status=excluded.status,
        reason_code=excluded.reason_code,evidence_json=excluded.evidence_json,
        attempts=local_sib_issue_reviews.attempts+1,updated_at=CURRENT_TIMESTAMP""",
        (cycle, payroll, state, reason, evidence))
    if resolved or state == 'NOT_FOUND':
        db.execute('DELETE FROM local_sib_scan_issues WHERE cycle=? AND payroll_id=?', (cycle, payroll))
    else:
        db.execute("INSERT OR REPLACE INTO local_sib_scan_issues(cycle,payroll_id,reason) VALUES(?,?,?)",
                   (cycle, payroll, reason))
