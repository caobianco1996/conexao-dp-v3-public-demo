"""Read-only SIB Bridge and Selecty clients for the Conexão DP V3."""

from __future__ import annotations

import json
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


SIB_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SELECTY_BASE_URL = "https://api.selecty.app/v2"
SELECTY_STATUSES = frozenset({"pending", "pre-exported", "exported", "processing", "accepted", "rejected"})
_TLS_CONTEXT_TTL_SECONDS = 300
_TLS_CONTEXT_CACHE_LIMIT = 8
_tls_context_cache = OrderedDict()
_tls_context_lock = threading.Lock()


class IntegrationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 0, *, request_id: str = "", retryable: bool = False):
        super().__init__(" ".join(str(message or "Falha na integração.").split())[:300])
        self.status_code = int(status_code or 0)
        self.retryable = bool(retryable)
        self.request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id) else ""


def _json_response(raw: bytes, provider: str) -> Any:
    if len(raw) > SIB_MAX_RESPONSE_BYTES:
        raise IntegrationError(f"A resposta {provider} excedeu o limite permitido.")
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IntegrationError(f"A resposta {provider} não veio em JSON válido.") from error


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


class _BridgeHTTPSConnection(http.client.HTTPSConnection):
    """Try DNS addresses with TLS validation, preferring a recently working one."""

    _preferred = {}
    _preferred_lock = threading.Lock()

    def connect(self):
        if self._tunnel_host:
            return super().connect()
        addresses = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        if len(addresses) <= 1:
            return super().connect()

        key = (self.host, self.port)
        with self._preferred_lock:
            preferred, expires = self._preferred.get(key, (None, 0))
        if expires > time.monotonic():
            addresses.sort(key=lambda item: item[4] != preferred)

        errors = []
        address_timeout = min(float(self.timeout or 20), 4.0)
        for family, socktype, proto, _canonname, sockaddr in addresses:
            connection = socket.socket(family, socktype, proto)
            connection.settimeout(address_timeout)
            try:
                if self.source_address:
                    connection.bind(self.source_address)
                connection.connect(sockaddr)
                # A serial Bridge can accept TCP while its previous query is
                # finishing. TLS needs the configured timeout, not the shorter
                # timeout used to skip an unreachable DNS address.
                connection.settimeout(self.timeout)
                try:
                    connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass
                self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
                self.sock.settimeout(self.timeout)
                with self._preferred_lock:
                    self._preferred[key] = (sockaddr, time.monotonic() + 300)
                return
            except (OSError, ssl.SSLError) as error:
                connection.close()
                self.sock = None
                errors.append(error)
        if errors:
            raise errors[-1]
        return super().connect()


class _BridgeHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_BridgeHTTPSConnection, req, context=self._context)


def _validated_ssl_context(ca_bundle_path: str = "") -> ssl.SSLContext:
    """Reuse immutable, verified TLS configuration without retaining stale CA files."""
    path = Path(ca_bundle_path).resolve() if ca_bundle_path else None
    key = str(path) if path else ""
    stat = path.stat() if path else None
    signature = (stat.st_mtime_ns, stat.st_size) if stat else None
    now = time.monotonic()
    with _tls_context_lock:
        cached = _tls_context_cache.get(key)
        if cached and cached[0] == signature and cached[1] > now:
            _tls_context_cache.move_to_end(key)
            return cached[2]
        # A changed/deleted CA never falls back to an older cached trust store.
        _tls_context_cache.pop(key, None)
        context = ssl.create_default_context(cafile=key or None)
        # HTTPSHandler only reads this context; certificate and hostname checks
        # remain those of create_default_context throughout its short lifetime.
        _tls_context_cache[key] = (signature, now + _TLS_CONTEXT_TTL_SECONDS, context)
        for expired_key, entry in list(_tls_context_cache.items()):
            if entry[1] <= now:
                del _tls_context_cache[expired_key]
        while len(_tls_context_cache) > _TLS_CONTEXT_CACHE_LIMIT:
            _tls_context_cache.popitem(last=False)
        return context


def _https_transport(method: str, url: str, headers: dict[str, str], body: bytes | None,
                     timeout: int, *, ca_bundle_path: str = "", dns_failover: bool = False) -> tuple[int, bytes, dict[str, str]]:
    request = Request(url, data=body, headers=headers, method=method)
    context = _validated_ssl_context(ca_bundle_path)
    handler = _BridgeHTTPSHandler if dns_failover else HTTPSHandler
    opener_handlers = [_NoRedirect()]
    if dns_failover:
        host = (urlsplit(url).hostname or "").rstrip(".").lower()
        is_local_name = host == "localhost" or host.endswith((".localhost", ".local"))
        try:
            address = ipaddress.ip_address(host.split("%", 1)[0])
            is_local_address = address.is_private or address.is_loopback or address.is_link_local
        except ValueError:
            is_local_address = False
        if is_local_name or is_local_address:
            # Internal SIB Bridges must be reached directly; a configured
            # HTTP proxy may be unavailable or unable to route private hosts.
            opener_handlers.insert(0, ProxyHandler({}))
    opener_handlers.append(handler(context=context))
    opener = build_opener(*opener_handlers)
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(SIB_MAX_RESPONSE_BYTES + 1)
            return int(response.status), data, {str(k).lower(): str(v) for k, v in response.headers.items()}
    except HTTPError as error:
        try:
            data = error.read(min(SIB_MAX_RESPONSE_BYTES + 1, 8192))
        finally:
            error.close()
        return int(error.code), data, {str(k).lower(): str(v) for k, v in error.headers.items()}
    except (URLError, TimeoutError, socket.timeout, ssl.SSLError, OSError) as error:
        reason = error.reason if isinstance(error, URLError) else error
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise IntegrationError("O certificado TLS do serviço não foi validado. Confira o CA PEM e o nome do servidor.") from error
        if isinstance(reason, socket.gaierror):
            message = "O nome do servidor não foi resolvido na rede."
        elif isinstance(reason, TimeoutError):
            message = "O serviço externo não respondeu dentro do tempo limite."
        elif isinstance(reason, ConnectionRefusedError):
            message = "A conexão foi recusada; o serviço pode estar indisponível."
        else:
            message = "A conexão com o serviço foi interrompida."
        raise IntegrationError(message, retryable=True) from error


def validate_sib_base_url(value: str) -> str:
    candidate = str(value or "").strip().rstrip("/")
    parsed = urlsplit(candidate)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("A URL da SIB Bridge deve usar HTTPS e conter apenas host e porta, sem credenciais ou caminho.")
    try:
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
    except ValueError as error:
        raise ValueError("A porta da SIB Bridge é inválida.") from error
    return candidate


@dataclass(frozen=True, slots=True)
class SibConfig:
    base_url: str
    api_key: str
    timeout_seconds: int = 20
    cf_client_id: str = ""
    cf_client_secret: str = ""
    ca_bundle_path: str = ""


class SibClient:
    """Only documented Bridge read/query endpoints are exposed by this client."""

    _request_locks = {}
    _request_locks_guard = threading.Lock()

    @classmethod
    def _request_lock(cls, base_url):
        parsed = urlsplit(base_url)
        key = (parsed.hostname, parsed.port or 443)
        with cls._request_locks_guard:
            return cls._request_locks.setdefault(key, threading.Lock())

    def __init__(self, config: SibConfig, *, transport: Callable[..., tuple[int, bytes, dict[str, str]]] | None = None):
        self.config = config
        self.transport = transport

    def _request(self, method: str, path: str, body: Mapping[str, Any] | None = None,
                 *, params: Mapping[str, Any] | None = None) -> Any:
        allowed = {
            ("GET", "/health"),
            ("GET", "/v1/catalogo/status"),
            ("GET", "/v1/catalogo/empresas"),
            ("GET", "/v1/catalogo/cadastros"),
            ("POST", "/v1/vinculos/resolver"),
            ("POST", "/v1/vinculos/consultar-lote"),
            ("POST", "/v1/vinculos/resolver-lote"),
            ("POST", "/v1/afastamentos/consultar-lote"),
        }
        dynamic_employee = method == "GET" and bool(re.fullmatch(r"/v1/vinculos/[A-Za-z0-9._-]{1,80}", path))
        dynamic_absence = method == "GET" and bool(re.fullmatch(r"/v1/afastamentos/\d{1,80}", path))
        if (method, path) not in allowed and not dynamic_employee and not dynamic_absence:
            raise IntegrationError("Operação SIB não permitida.")
        base_url = validate_sib_base_url(self.config.base_url)
        if not self.config.api_key or any(char in self.config.api_key for char in "\r\n\0"):
            raise IntegrationError("A chave da SIB Bridge não está configurada.")
        timeout = max(3, min(int(self.config.timeout_seconds), 60))
        headers = {"Accept": "application/json", "X-API-Key": self.config.api_key}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.config.cf_client_id:
            headers["CF-Access-Client-Id"] = self.config.cf_client_id
        if self.config.cf_client_secret:
            headers["CF-Access-Client-Secret"] = self.config.cf_client_secret
        url = base_url + path
        if params:
            url += "?" + urlencode(params)
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        if self.transport:
            status, raw, response_headers = self.transport(method, url, headers, payload, timeout)
        else:
            # Bridge 6.6 processes accepted clients sequentially. Serialize all
            # V3 reads to this origin, including health checks from other tabs.
            with self._request_lock(base_url):
                status, raw, response_headers = _https_transport(
                    method, url, headers, payload, timeout, ca_bundle_path=self.config.ca_bundle_path,
                    dns_failover=True,
                )
        parsed = _json_response(raw, "SIB Bridge")
        if not 200 <= int(status) < 300:
            message = f"SIB Bridge respondeu HTTP {status}."
            if int(status) in (401, 403):
                message = "A SIB Bridge recusou a autenticação. Confira a chave e o acesso de rede."
            elif int(status) == 404:
                message = "A rota solicitada não existe nesta versão da SIB Bridge."
            raise IntegrationError(message, int(status), request_id=parsed.get("request_id", "") if isinstance(parsed, Mapping) else "",
                                   retryable=int(status) in (408, 429, 502, 503, 504))
        if isinstance(parsed, Mapping) and parsed.get("error"):
            raise IntegrationError("A SIB Bridge informou uma falha na consulta.", int(status))
        return parsed

    def health(self) -> dict[str, Any]:
        payload = self._request("GET", "/health")
        if not isinstance(payload, Mapping):
            raise IntegrationError("A verificação de saúde da SIB Bridge retornou formato inesperado.")
        return {"connected": True, "status": str(payload.get("status") or "ok")[:40]}

    def get_catalog_page(self, dataset: str, *, cursor: str = "") -> dict[str, Any]:
        routes = {"companies": "/v1/catalogo/empresas", "employments": "/v1/catalogo/cadastros"}
        if dataset not in routes or not isinstance(cursor, str) or len(cursor) > 8192:
            raise ValueError("Etapa ou continuação da leitura SIB inválida.")
        try:
            result = self._request("GET", routes[dataset], params={"limit": 50, **({"cursor": cursor} if cursor else {})})
        except IntegrationError as error:
            if error.status_code == 404:
                raise IntegrationError("Instale a extensão de catálogo na Bridge do servidor para trazer empresas e cadastros por lote.", 404) from error
            if error.status_code in (400, 409):
                raise IntegrationError("A continuação SIB expirou ou a origem mudou. Use Iniciar nova leitura; os cadastros já trazidos serão preservados.", error.status_code) from error
            raise
        if not isinstance(result, dict):
            raise IntegrationError("A listagem de cadastros SIB retornou formato inesperado.")
        return result

    def get_identity_page(self, *, cursor: str = "") -> dict[str, Any]:
        if not isinstance(cursor, str) or len(cursor) > 8192:
            raise ValueError('Continuação do inventário inválida.')
        if not cursor:
            status = self._request('GET', '/v1/catalogo/status')
            if not isinstance(status, Mapping) or status.get('identity_inventory') != 'sib-identities-v1':
                raise IntegrationError('A Bridge precisa da extensão de inventário leve para detectar números antigos preenchidos e exclusões. Novas matrículas continuam sendo consultadas.', 501)
        result = self._request('GET', '/v1/catalogo/cadastros', params={'view': 'identities', 'limit': 1000, **({'cursor': cursor} if cursor else {})})
        if not isinstance(result, dict):
            raise IntegrationError('O inventário SIB retornou formato inesperado.')
        return result

    def get_employee(self, id_folha: str) -> dict[str, Any]:
        identifier = str(id_folha or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", identifier):
            raise ValueError("Informe um ID Folha válido para consultar no SIB.")
        payload = self._request("GET", f"/v1/vinculos/{identifier}")
        if not isinstance(payload, Mapping):
            raise IntegrationError("A consulta SIB retornou um vínculo em formato inesperado.")
        return normalize_sib_employee(payload, identifier)

    def get_batch(self, ids_folha: list[str]) -> list[dict[str, Any]]:
        identifiers = list(dict.fromkeys(str(item or "").strip() for item in ids_folha))
        if not identifiers or len(identifiers) > 50 or any(not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", item) for item in identifiers):
            raise ValueError("Informe de 1 a 50 IDs Folha válidos para a consulta em lote.")
        values = [int(item) if item.isdigit() else item for item in identifiers]
        payload = self._request("POST", "/v1/vinculos/consultar-lote", {"ids": values, "ids_folha": values})
        rows = payload
        if isinstance(payload, Mapping):
            rows = payload.get("items") or payload.get("data") or payload.get("vinculos") or payload.get("resultados") or []
        if not isinstance(rows, list):
            raise IntegrationError("A consulta SIB em lote retornou formato inesperado.")
        result = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            identifier = str(row.get("id_folha") or "")
            nested = row.get("data")
            employee = normalize_sib_employee(nested if isinstance(nested, Mapping) else row, identifier)
            if "data" in row:
                employee["found"] = isinstance(nested, Mapping) and not bool(row.get("error"))
            if row.get("error"):
                employee["error"] = str(row["error"])[:120]
            result.append(employee)
        return result

    def get_scan_batch(self, ids_folha: list[str]) -> list[dict[str, Any]]:
        """Query existing IDs while preserving missing and ambiguous results."""
        identifiers = list(dict.fromkeys(_scan_id(item) for item in ids_folha))
        if not identifiers or len(identifiers) > 50:
            raise ValueError("Informe de 1 a 50 matrículas para consultar no SIB.")
        payload = self._request("POST", "/v1/vinculos/consultar-lote", {"ids": [int(item) for item in identifiers]})
        rows = payload.get("items") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list) or len(rows) != len(identifiers):
            raise IntegrationError("A Bridge não confirmou uma resposta para cada matrícula do lote.")
        found = {}
        for row in rows:
            if not isinstance(row, Mapping) or "id_folha" not in row:
                raise IntegrationError("A consulta SIB retornou uma matrícula sem identificação.")
            try:
                identifier = _scan_id(row["id_folha"])
            except ValueError as error:
                raise IntegrationError("A consulta SIB retornou uma matrícula inválida.") from error
            if identifier not in identifiers or identifier in found:
                raise IntegrationError("A Bridge repetiu ou trocou matrículas no lote.")
            data, error = row.get("data"), row.get("error")
            if error is not None and (not isinstance(error, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", error)):
                raise IntegrationError("A Bridge retornou um erro de matrícula em formato inesperado.")
            if data is not None:
                if error or not isinstance(data, Mapping) or not isinstance(data.get("employee"), Mapping):
                    raise IntegrationError("A consulta SIB retornou um cadastro em formato inesperado.")
                try:
                    actual = _scan_id(data["employee"].get("id_folha"))
                except ValueError as problem:
                    raise IntegrationError("A Bridge devolveu um cadastro sem matrícula válida.") from problem
                if actual != identifier:
                    raise IntegrationError("A matrícula devolvida pela Bridge difere da solicitada.")
            found[identifier] = {"id_folha": identifier, "data": dict(data) if data is not None else None, "error": error}
        return [found[identifier] for identifier in identifiers]

    def get_employee_raw(self, id_folha: str, *, contract_type_code: str = "",
                         branch_code: str = "", employer_code: str = "") -> dict[str, Any]:
        """Read full registration through the already installed Bridge endpoint."""
        compound = re.fullmatch(r'(\d{1,10})-(\d{1,10})', str(id_folha or '').strip())
        if compound:
            prefix, numeric = compound.groups()
            if branch_code and _scan_id(branch_code) != _scan_id(prefix):
                raise ValueError('A filial informada difere do prefixo da matrícula.')
            branch_code, id_folha = _scan_id(prefix), numeric
        identifier = _scan_id(id_folha)
        branch = _scan_id(branch_code) if str(branch_code or '').strip() else ''
        employer = _scan_id(employer_code) if str(employer_code or '').strip() else ''
        modality = str(contract_type_code or "").strip()
        if modality and not re.fullmatch(r"\d{1,3}", modality):
            raise ValueError("Informe um código de vínculo SIB válido.")
        params = {'raw': 1, **({'vin_fun': modality} if modality else {}),
                  **({'fil_fun': branch} if branch else {}), **({'emp_fun': employer} if employer else {})}
        payload = self._request('GET', f'/v1/vinculos/{identifier}', params=params)
        if not isinstance(payload, dict) or not isinstance(payload.get("raw_employee"), Mapping):
            raise IntegrationError("A Bridge não disponibilizou a ficha completa desta matrícula.")
        raw = payload["raw_employee"]
        if any(key not in raw for key in ("EMP_FUN", "FIL_FUN", "COD_FUN")):
            raise IntegrationError("A ficha SIB não contém empresa, filial e matrícula para identificar o vínculo.")
        try:
            actual = _scan_id(raw["COD_FUN"])
        except ValueError as error:
            raise IntegrationError("A ficha SIB retornou uma matrícula inválida.") from error
        if actual != identifier:
            raise IntegrationError("A ficha SIB retornada não corresponde à matrícula solicitada.")
        if modality and str(raw.get("VIN_FUN", "")).strip() != modality:
            raise IntegrationError("A Bridge não confirmou matrícula e vínculo juntos. A ficha de outra modalidade foi preservada.", 409)
        if ((branch and _scan_id(raw['FIL_FUN']) != branch)
                or (employer and _scan_id(raw['EMP_FUN']) != employer)):
            raise IntegrationError('A ficha retornada pertence a outra empresa ou filial. O código completo não foi confirmado.', 409)
        return payload

    def identify_employee(self, id_folha: str, *, cpf: str, admission_date: str,
                          taker_cnpj: str) -> dict[str, Any]:
        """Confirm one canonical record with the existing exact-identity batch API."""
        identifier = _scan_id(id_folha)
        cpf_value = re.sub(r"\D", "", str(cpf or ""))
        cnpj_value = re.sub(r"\D", "", str(taker_cnpj or ""))
        admission_value = _identity_date(admission_date)
        if len(cpf_value) != 11 or len(cnpj_value) != 14 or not admission_value:
            raise ValueError("Informe CPF completo, data de admissão válida e CNPJ completo do tomador para identificar o vínculo.")
        record = {"id_folha": int(identifier), "cpf": cpf_value,
                  "data_admissao": admission_value, "cnpj_tomador": cnpj_value}
        payload = self._request("POST", "/v1/vinculos/consultar-lote",
                                {"ids": [int(identifier)], "records": [record]})
        rows = payload.get("items") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
            raise IntegrationError("A Bridge não confirmou um único resultado para a identificação do vínculo.")
        item = rows[0]
        try:
            actual = _scan_id(item.get("id_folha"))
        except ValueError as error:
            raise IntegrationError("A identificação SIB retornou uma matrícula inválida.") from error
        if actual != identifier:
            raise IntegrationError("A identificação SIB retornou uma matrícula diferente da solicitada.")
        reason = item.get("error")
        if reason == "vinculo_ambiguo":
            raise IntegrationError("A matrícula permanece ambígua mesmo com CPF, admissão e CNPJ. Nenhum vínculo foi confirmado.")
        if reason == "identidade_nao_corresponde":
            raise IntegrationError("CPF, admissão e CNPJ não correspondem a um vínculo único dessa matrícula no SIB.")
        if reason is not None:
            raise IntegrationError("A Bridge não concluiu a identificação do vínculo. Nenhum cadastro foi confirmado.")
        data = item.get("data")
        employee = data.get("employee") if isinstance(data, Mapping) else None
        client = data.get("client") if isinstance(data, Mapping) else None
        if not isinstance(employee, Mapping) or not isinstance(client, Mapping):
            raise IntegrationError("O SIB não retornou funcionário e empresa suficientes para confirmar esse vínculo.")
        try:
            employee_id = _scan_id(employee.get("id_folha"))
        except ValueError as error:
            raise IntegrationError("O cadastro identificado pelo SIB não possui matrícula válida.") from error
        actual_cpf = re.sub(r"\D", "", str(employee.get("cpf") or ""))
        if 0 < len(actual_cpf) < 11:
            actual_cpf = actual_cpf.zfill(11)
        actual_date = _identity_date(employee.get("admission_date"))
        actual_cnpj = re.sub(r"\D", "", str(client.get("cnpj") or ""))
        if (employee_id != identifier or actual_cpf != cpf_value
                or actual_date != admission_value or actual_cnpj != cnpj_value):
            raise IntegrationError("O SIB não confirmou CPF, admissão e CNPJ completo exatamente como informados. Nenhum vínculo foi confirmado.")
        return dict(data)

    def resolve(self, *, cpf: str, admission_date: str, taker_cnpj: str) -> dict[str, Any]:
        digits = lambda value: re.sub(r"\D", "", str(value or ""))
        cpf_value, cnpj_value = digits(cpf), digits(taker_cnpj)
        if len(cpf_value) != 11 or len(cnpj_value) != 14 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", admission_date or ""):
            raise ValueError("Para conciliar, informe CPF, data de admissão e CNPJ do tomador válidos.")
        payload = self._request("POST", "/v1/vinculos/resolver", {
            "cpf": cpf_value, "data_admissao": admission_date, "cnpj_tomador": cnpj_value,
        })
        if not isinstance(payload, Mapping):
            raise IntegrationError("O resolvedor SIB retornou formato inesperado.")
        return normalize_sib_employee(payload, "")

    def get_absences(self, id_folha: str) -> list[dict[str, Any]]:
        identifier = str(id_folha or "").strip()
        if not identifier.isdigit() or len(identifier) > 80:
            raise ValueError("Informe um ID Folha numérico para consultar afastamentos.")
        payload = self._request("GET", f"/v1/afastamentos/{identifier}")
        rows = payload.get("items", payload) if isinstance(payload, Mapping) else payload
        if not isinstance(rows, list):
            raise IntegrationError("A rota de afastamentos SIB retornou formato inesperado.")
        if any(not isinstance(row, Mapping) for row in rows):
            raise IntegrationError("A Bridge retornou um afastamento inválido.")
        return [normalize_absence(row) for row in rows]

    def get_absences_batch(self, ids_folha: list[str]) -> list[dict[str, Any]]:
        identifiers = list(dict.fromkeys(_scan_id(item) for item in ids_folha))
        if not identifiers or len(identifiers) > 50:
            raise ValueError("Informe de 1 a 50 matrículas para consultar afastamentos.")
        values = [int(item) for item in identifiers]
        payload = self._request("POST", "/v1/afastamentos/consultar-lote", {"ids": values, "ids_folha": values})
        rows = payload.get("items") if isinstance(payload, Mapping) else payload
        if not isinstance(rows, list) or len(rows) != len(identifiers):
            raise IntegrationError("A Bridge não confirmou cada matrícula no lote de afastamentos.")
        found = {}
        for row in rows:
            if not isinstance(row, Mapping):
                raise IntegrationError("Envelope de afastamentos inválido.")
            try:
                identifier = _scan_id(row.get("id_folha"))
            except ValueError as error:
                raise IntegrationError("Envelope de afastamentos sem matrícula válida.") from error
            if identifier not in identifiers or identifier in found:
                raise IntegrationError("A Bridge repetiu ou trocou matrículas no lote de afastamentos.")
            error = row.get("error")
            data = row.get("data")
            if error:
                found[identifier] = {"id_folha": identifier, "data": None, "error": "Consulta de afastamentos não confirmada pela Bridge."}
            elif not isinstance(data, list) or any(not isinstance(item, Mapping) for item in data):
                found[identifier] = {"id_folha": identifier, "data": None, "error": "Lista de afastamentos ausente ou inválida."}
            else:
                found[identifier] = {"id_folha": identifier, "data": [normalize_absence(item) for item in data], "error": None}
        return [found[identifier] for identifier in identifiers]


def _scan_id(value: Any) -> str:
    candidate = "" if value is None or isinstance(value, bool) else str(value).strip()
    if not re.fullmatch(r"\d{1,10}", candidate):
        raise ValueError("Informe uma matrícula SIB numérica válida.")
    return str(int(candidate))


def _identity_date(value: Any) -> str:
    """Calendar-valid ISO date for comparisons; an invalid value never matches."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    candidate = "" if value is None else str(value).strip()
    if not candidate:
        return ""
    if candidate.isdigit() and len(candidate) != 8:
        return ""
    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    patterns = ("%Y%m%d", "%d%m%Y", "%d/%m/%Y", "%Y/%m/%d", "%d/%m/%Y %H:%M:%S")
    if re.fullmatch(r"\d{8}", candidate) and not candidate.startswith(("19", "20")):
        patterns = ("%d%m%Y", "%Y%m%d", *patterns[2:])
    for pattern in patterns:
        try:
            return datetime.strptime(candidate, pattern).date().isoformat()
        except ValueError:
            continue
    return ""


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        current: Any = payload
        for part in key.split("."):
            if not isinstance(current, Mapping) or part not in current:
                current = None
                break
            current = current[part]
        if current not in (None, "", [], {}):
            return current
    return ""


def _unwrap(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    current = payload
    for _ in range(4):
        nested = next((current[key] for key in ("data", "vinculo", "resultado", "employee")
                       if isinstance(current.get(key), Mapping)), None)
        if nested is None:
            break
        current = nested
    return current


def normalize_sib_employee(payload: Mapping[str, Any], fallback_id: str = "") -> dict[str, Any]:
    data = _unwrap(payload)
    cpf = re.sub(r"\D", "", str(_first(data, "cpf", "document", "cpf_numero") or ""))
    if 0 < len(cpf) < 11:
        cpf = cpf.zfill(11)
    return {
        "idFolha": str(_first(data, "id_folha", "codigo_folha", "registration", "employee_id") or fallback_id)[:80],
        "name": str(_first(data, "name", "nome", "nome_completo", "employee_name"))[:180],
        "cpf": cpf[:11],
        "company": str(_first(payload, "client.legal_name", "client.name", "cliente.razao_social", "cliente.nome", "tomador.razao_social", "tomador.nome"))[:180],
        "takerCnpj": re.sub(r"\D", "", str(_first(payload, "client.cnpj", "cliente.cnpj", "tomador.cnpj") or ""))[:14],
        "admissionDate": str(_first(data, "data_admissao", "admission_date", "hire_date", "start_date"))[:10],
        "bank": str(_first(data, "dados_bancarios.banco.codigo", "dados_bancarios.banco.nome", "banco.codigo", "bank.code", "codigo_banco", "bank_code", "banco"))[:100],
        "bankBranch": str(_first(data, "dados_bancarios.agencia", "banco.agencia", "bank.branch", "bank_branch", "agencia"))[:40],
        "bankAccount": str(_first(data, "dados_bancarios.conta", "banco.conta", "bank.account", "bank_account", "conta"))[:60],
        "accountDigit": str(_first(data, "dados_bancarios.digito", "dados_bancarios.digito_conta", "banco.digito", "bank_digit", "digito_conta"))[:12],
        "dismissalDate": str(_first(data, "dados_demissionais.data_demissao", "demissao.data", "desligamento.data", "data_demissao", "dismissal_date", "termination_date"))[:10],
        "dismissalReasonCode": str(_first(data, "dados_demissionais.motivo_codigo", "dados_demissionais.motivo_demissao_codigo", "demissao.motivo_codigo", "motivo_demissao_codigo", "termination_reason"))[:20],
        "dismissalReason": str(_first(data, "dados_demissionais.motivo_descricao", "demissao.motivo_descricao", "desligamento.motivo_descricao", "termination_reason_source_text"))[:180],
        "dismissalReasonOrigin": str(_first(data, "termination_reason_source_code", "motivo_demissao_origem_codigo"))[:40],
        "terminationType": str(_first(data, "termination_type_code", "tipo_termino_codigo"))[:40],
    }


def normalize_absence(payload: Mapping[str, Any]) -> dict[str, Any]:
    metadata_keys = (
        'dias', 'cid', 'cid_correlacionado', 'acidente_trabalho', 'tipo_acidente_transito',
        'descricao_tipo_acidente_transito', 'codigo_medico', 'medico', 'mesmo_motivo_60_dias',
        'detalhamento', 'observacao', 'observacao_fim', 'data_alteracao_motivo', 'origem_alteracao',
        'efeito_retroativo', 'motivo_anterior', 'descricao_motivo_anterior',
        'nao_considerar_entrada_inss', 'nao_somar_periodos', 'aplicar_suspensao_insalubridade',
        'calcular_beneficios',
    )
    return {
        "id": str(_first(payload, "sequencia", "id", "absence_id", "codigo") if _first(payload, "sequencia", "id", "absence_id", "codigo") is not None else "")[:80],
        "idFolha": str(_first(payload, "id_folha", "employee_id", "registration") or "")[:80],
        "startDate": _identity_date(_first(payload, "inicio", "data_inicio", "start_date", "start")),
        "endDate": _identity_date(_first(payload, "fim", "data_fim", "end_date", "finish")),
        "invalidEndDate": bool(_first(payload, "fim", "data_fim", "end_date", "finish") and not _identity_date(_first(payload, "fim", "data_fim", "end_date", "finish"))),
        "status": str(_first(payload, "status", "situacao") or "")[:80],
        "reasonCode": str(_first(payload, "codigo_motivo", "reason_code") or "")[:40],
        "reason": str(_first(payload, "descricao_motivo", "motivo", "descricao", "reason") or "")[:180],
        "employerCode": str(_first(payload, "employer_code", "empresa", "EMP_FUN") or "")[:40],
        "branchCode": str(_first(payload, "branch_code", "filial", "FIL_FUN") or "")[:40],
        "metadata": {key: str(payload[key])[:2000] for key in metadata_keys
                     if payload.get(key) not in (None, "", [], {}) and not isinstance(payload[key], (dict, list))},
    }


@dataclass(frozen=True, slots=True)
class SelectyConfig:
    api_id: str
    secret: str
    base_url: str = SELECTY_BASE_URL
    timeout_seconds: int = 20


class SelectyClient:
    """Official v2 client. The business surface intentionally contains GET only."""

    def __init__(self, config: SelectyConfig, *, transport: Callable[..., tuple[int, bytes, dict[str, str]]] | None = None):
        self.config = config
        self.transport = transport
        self._token = ""
        self._expires_at = 0.0
        self._last_call = 0.0

    def _send(self, method: str, path: str, *, body: Mapping[str, Any] | None = None,
              params: Mapping[str, Any] | None = None, token: str = "") -> tuple[int, Any, dict[str, str]]:
        if method == "POST" and path != "auth":
            raise IntegrationError("Esta versão da integração Selecty bloqueia operações externas de escrita.")
        if method not in ("GET", "POST"):
            raise IntegrationError("Método externo não permitido nesta integração Selecty.")
        if self.config.base_url.rstrip("/") != SELECTY_BASE_URL:
            raise ValueError("A integração V3 usa somente a API oficial Selecty v2.")
        url = SELECTY_BASE_URL + "/" + path.lstrip("/")
        if params:
            url += "?" + urlencode({key: value for key, value in params.items() if value not in (None, "")})
        headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "Conexao-DP-V3/1.0"}
        if token:
            headers["Authorization"] = "Bearer " + token
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        if self.transport:
            status, raw, response_headers = self.transport(method, url, headers, payload, max(3, min(int(self.config.timeout_seconds), 60)))
        else:
            now = time.monotonic()
            wait = 0.75 - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
            status, raw, response_headers = _https_transport(
                method, url, headers, payload, max(3, min(int(self.config.timeout_seconds), 60)),
            )
            self._last_call = time.monotonic()
        parsed = _json_response(raw, "Selecty")
        headers_lower = {str(k).lower(): str(v) for k, v in (response_headers or {}).items()}
        if not 200 <= int(status) < 300:
            if int(status) == 401:
                message = "A Selecty recusou a autenticação. Verifique API ID, Secret e permissões da conta."
            elif int(status) == 429:
                retry = headers_lower.get("retry-after", "").strip()
                message = "A Selecty limitou temporariamente as consultas" + (f"; aguarde {retry} segundo(s)." if retry.isdigit() else "; aguarde antes de consultar novamente.")
            else:
                message = f"A Selecty respondeu HTTP {int(status)} à consulta."
            raise IntegrationError(message, int(status))
        if isinstance(parsed, Mapping) and parsed.get("error"):
            raise IntegrationError("A Selecty informou uma falha na consulta.", int(status))
        return int(status), parsed, headers_lower

    def authenticate(self, *, force: bool = False) -> str:
        if not self.config.api_id or not self.config.secret:
            raise IntegrationError("API ID e Secret Selecty ainda não estão configurados.")
        if not force and self._token and time.monotonic() < self._expires_at:
            return self._token
        _, payload, _ = self._send("POST", "auth", body={"api_id": self.config.api_id, "secret": self.config.secret})
        if not isinstance(payload, Mapping) or not str(payload.get("access_token") or "").strip():
            raise IntegrationError("A autenticação Selecty não retornou um token válido.")
        if str(payload.get("token_type") or "bearer").lower() != "bearer":
            raise IntegrationError("A Selecty retornou um tipo de autenticação não suportado.")
        try:
            expires = max(60, min(int(payload.get("expires_in") or 3600), 86400))
        except (TypeError, ValueError):
            expires = 3600
        self._token = str(payload["access_token"]).strip()
        self._expires_at = time.monotonic() + max(30, expires - 60)
        return self._token

    def _get_authorized(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        token = self.authenticate()
        try:
            return self._send("GET", path, params=params, token=token)[1]
        except IntegrationError as error:
            if error.status_code != 401:
                raise
            token = self.authenticate(force=True)
            return self._send("GET", path, params=params, token=token)[1]

    def list_payroll(self, *, status: str = "exported", page: int = 1, cpf: str = "", vacancy_id: str = "") -> dict[str, Any]:
        if status not in SELECTY_STATUSES or not 1 <= int(page) <= 10000:
            raise ValueError("Status ou página da consulta Selecty inválido.")
        cpf_digits = re.sub(r"\D", "", str(cpf or ""))
        if cpf_digits and len(cpf_digits) != 11:
            raise ValueError("O filtro CPF deve conter 11 dígitos.")
        payload = self._get_authorized("work/payroll", {
            "sort": "-work_id", "page": int(page), "status": status, "cpf": cpf_digits,
            "vacancy_id": str(vacancy_id or "")[:80],
        })
        if not isinstance(payload, Mapping) or not isinstance(payload.get("data", []), list):
            raise IntegrationError("A listagem Selecty retornou formato inesperado.")
        rows = [normalize_selecty_payroll(row) for row in payload.get("data", []) if isinstance(row, Mapping)]
        meta = payload.get("meta") if isinstance(payload.get("meta"), Mapping) else {}
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), Mapping) else {}
        return {
            "items": rows,
            "page": int(pagination.get("current_page") or meta.get("current_page") or page),
            "lastPage": int(pagination.get("last_page") or meta.get("last_page") or page),
            "total": int(pagination.get("total") or meta.get("total") or len(rows)),
        }

    def payroll_details(self, work_id: str) -> dict[str, Any]:
        identifier = str(work_id or "").strip()
        if not identifier.isdigit() or len(identifier) > 20:
            raise ValueError("O work_id Selecty deve ser numérico.")
        payload = self._get_authorized(f"work/payroll/{identifier}")
        if not isinstance(payload, Mapping):
            raise IntegrationError("O detalhe Selecty retornou formato inesperado.")
        return normalize_selecty_details(payload)


def _mask_cpf(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return f"***.***.***-{digits[-2:]}" if len(digits) == 11 else ""


def normalize_selecty_payroll(payload: Mapping[str, Any]) -> dict[str, Any]:
    person = payload.get("person") if isinstance(payload.get("person"), Mapping) else {}
    vacancy = payload.get("vacancy") if isinstance(payload.get("vacancy"), Mapping) else {}
    company = payload.get("company") if isinstance(payload.get("company"), Mapping) else {}
    cpf = str(_first(payload, "cpf", "document", "person.cpf", "person.document") or "")
    return {
        "workId": str(_first(payload, "work_id", "employmentId", "employment_id") or "")[:40],
        "personId": str(_first(payload, "person_id", "personId", "person.id") or "")[:40],
        "status": str(_first(payload, "status", "stage") or "")[:60],
        "name": str(_first(payload, "name", "person.name", "person.full_name", "employee_name") or "")[:180],
        "cpfMasked": _mask_cpf(cpf),
        "vacancyId": str(_first(payload, "vacancy_id", "vacancyId", "vacancy.id") or "")[:40],
        "vacancy": str(_first(payload, "vacancy_title", "vacancy.name", "position", "job_title") or "")[:180],
        "company": str(_first(payload, "company_name", "company.name", "client.name") or "")[:180],
        "registrationCode": str(_first(payload, "employee_registration_code", "registration_code") or "")[:80],
        "admissionDate": str(_first(payload, "start", "start_date", "admission_date", "finish") or "")[:20],
    }


def normalize_selecty_details(payload: Mapping[str, Any]) -> dict[str, Any]:
    details = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    item = normalize_selecty_payroll(details)
    person = details.get("person") if isinstance(details.get("person"), Mapping) else {}
    item["email"] = str(_first(details, "email", "person.email") or "")[:254]
    item["phone"] = str(_first(details, "phone", "person.phone", "mobile") or "")[:40]
    item["takerCnpj"] = re.sub(r"\D", "", str(_first(details, "client.cnpj", "company.cnpj", "service_taker.cnpj") or ""))[:14]
    item["position"] = str(_first(details, "position", "job_title", "vacancy.name") or "")[:180]
    item["contractType"] = str(_first(details, "contract_type", "employment_type") or "")[:100]
    item["rawDocumentCount"] = len(details.get("documents", [])) if isinstance(details.get("documents"), list) else 0
    return item

