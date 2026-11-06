"""Pistas de identidade de lotes V2 publicados para confirmar consultas no SIB.

Nenhum cadastro é criado a partir destas pistas. A Bridge ainda precisa confirmar
a identidade; empresa e filial da chave física nunca são inferidas aqui.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date


def _object(value):
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            result = json.loads(value)
        except (ValueError, TypeError):
            return None
        return result if isinstance(result, Mapping) else None
    return None


def _text(value):
    return "" if value is None or isinstance(value, bool) else str(value).strip()


def _payroll(value):
    candidate = _text(value)
    return str(int(candidate)) if re.fullmatch(r"[0-9]{1,10}", candidate) else None


def _test_flag(value):
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized in ("istest", "isteste", "test", "teste", "testdata", "testrecord", "testmode", "synthetic", "issynthetic"):
                if child is True or child == 1 or (isinstance(child, str) and child.strip().casefold() in ("true", "1", "yes", "sim", "test", "teste")):
                    return True
            if normalized in ("source", "sourcekind", "sourcetype", "origin", "originkind", "system"):
                marker = _text(child).upper()
                if marker in ("TEST", "TESTE", "TESTE_LOCAL", "TEST_LOCAL", "SYNTHETIC") or marker.startswith(("TESTE_", "TEST_")):
                    return True
            if _test_flag(child):
                return True
            if normalized in ("options", "metadata", "meta", "parsed", "data") and isinstance(child, str):
                expanded = _object(child)
                if expanded is not None and _test_flag(expanded):
                    return True
    elif isinstance(value, list):
        return any(_test_flag(child) for child in value)
    return False


def _document(value, size):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    candidate = re.sub(r"[.\-/\s]", "", _text(value))
    if not re.fullmatch(r"[0-9]{" + str(size) + r"}", candidate) or len(set(candidate)) == 1:
        return None
    if size == 11:
        for length in (9, 10):
            check = ((sum(int(candidate[index]) * (length + 1 - index) for index in range(length)) * 10) % 11) % 10
            if check != int(candidate[length]):
                return None
    else:
        for length, weights in ((12, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)), (13, (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2))):
            remainder = sum(int(candidate[index]) * weights[index] for index in range(length)) % 11
            if (0 if remainder < 2 else 11 - remainder) != int(candidate[length]):
                return None
    return candidate


def _admission(value):
    candidate = _text(value)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", candidate):
        return None
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return None


def find_identity_hints(db, payroll_id):
    """Return distinct validated published identities without selecting a person.

    Repeated rows with the same tuple are harmless. A conflicting or invalid
    non-test identity for the requested registration blocks automatic selection.
    No CPF, CNPJ, name or payload is logged by this module.
    """
    requested = _payroll(payroll_id)
    if requested is None:
        return []
    batches = {}
    for row in db.execute("SELECT payload FROM v2_source_rows WHERE table_name='import_batches'"):
        batch = _object(row[0])
        if batch is None:
            continue
        batch_id = _text(batch.get("id"))
        if not batch_id:
            continue
        published = (_text(batch.get("status")).upper() == "PUBLISHED"
                     and bool(_text(batch.get("published_at"))) and not _test_flag(batch))
        # Divergent metadata for the same batch never enables a hint.
        batches[batch_id] = published and batches.get(batch_id, True)
    identities = set()
    # The final Python comparison still rejects malformed values such as "12x".
    # SQLite only narrows the candidate rows without exposing identity values.
    candidates = db.execute("""WITH source_rows AS (
        SELECT CASE WHEN json_valid(payload) THEN payload ELSE '{}' END AS payload
        FROM v2_source_rows WHERE table_name='import_rows'
    ), parsed_rows AS (
        SELECT payload, CASE WHEN json_valid(json_extract(payload,'$.parsed'))
            THEN json_extract(payload,'$.parsed') ELSE '{}' END AS parsed FROM source_rows
    ) SELECT payload FROM parsed_rows
      WHERE CAST(json_extract(parsed,'$.data.externalCode') AS INTEGER)=?""", (int(requested),))
    for row in candidates:
        payload = _object(row[0])
        if payload is None or not batches.get(_text(payload.get("batch_id")), False):
            continue
        parsed = _object(payload.get("parsed"))
        data = _object(parsed.get("data")) if parsed is not None else None
        if data is None or _payroll(data.get("externalCode")) != requested:
            continue
        if _test_flag(payload) or _test_flag(data):
            continue
        cpf = _document(data.get("cpf"), 11)
        cnpj = _document(data.get("companyCnpj"), 14)
        admission = _admission(data.get("admissionDate"))
        if not cpf or not cnpj or not admission:
            return []
        identities.add((cpf, admission, cnpj))
    return [{"cpf": cpf, "admission_date": admission, "taker_cnpj": cnpj, "source": "V2 publicada"}
            for cpf, admission, cnpj in sorted(identities)]


def find_identity_hint(db, payroll_id):
    """Legacy single-person callers must still reject more than one identity."""
    hints = find_identity_hints(db, payroll_id)
    return hints[0] if len(hints) == 1 else None
