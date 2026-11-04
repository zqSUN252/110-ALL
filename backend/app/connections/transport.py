"""Bounded proxy/TLS/WebSocket diagnostics without logging in to an account."""
import asyncio
import base64
import hashlib
import math
import os
import socket
import ssl
import time
from urllib.parse import unquote, urlsplit


_TARGET = 'web.whatsapp.com'
_HEADER_LIMIT = 64 * 1024
_WEBSOCKET_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


class _InvalidResponse(Exception):
    pass


def _result(checked_at, category, tls_ok=False):
    ok = category == 'ok'
    return {'ok': ok, 'checked_at': checked_at, 'category': category,
            'tls_ok': bool(tls_ok), 'websocket_ok': ok}


def _proxy_address(proxy_url):
    if not isinstance(proxy_url, str) or len(proxy_url) > 4096 or any(
            char.isspace() or ord(char) < 32 or ord(char) == 127 for char in proxy_url):
        raise ValueError
    parsed = urlsplit(proxy_url)
    if (parsed.scheme != 'http' or not parsed.hostname or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment):
        raise ValueError
    port = parsed.port if parsed.port is not None else 80
    if not 1 <= port <= 65535:
        raise ValueError
    authorization = None
    if parsed.username is not None:
        username = unquote(parsed.username, errors='strict')
        password = unquote(parsed.password or '', errors='strict')
        if any(ord(char) < 32 or ord(char) == 127 for char in username + password):
            raise ValueError
        authorization = base64.b64encode((username + ':' + password).encode('utf-8')).decode('ascii')
    return parsed.hostname, port, authorization


def _remaining(deadline):
    budget = deadline - time.monotonic()
    if budget <= 0:
        raise TimeoutError
    return budget


def _send(sock, payload, deadline):
    sock.settimeout(_remaining(deadline))
    sock.sendall(payload)


def _read_headers(sock, deadline):
    data = bytearray()
    while True:
        end = data.find(b'\r\n\r\n')
        if end >= 0:
            if end + 4 > _HEADER_LIMIT:
                raise _InvalidResponse
            break
        if len(data) >= _HEADER_LIMIT:
            raise _InvalidResponse
        sock.settimeout(_remaining(deadline))
        block = sock.recv(min(4096, _HEADER_LIMIT - len(data)))
        if not block:
            raise _InvalidResponse
        data.extend(block)
    lines = bytes(data[:end]).decode('latin1').split('\r\n')
    status_line = lines[0].split(' ', 2)
    if (len(status_line) < 2 or status_line[0] not in ('HTTP/1.0', 'HTTP/1.1')
            or len(status_line[1]) != 3 or not status_line[1].isdigit()):
        raise _InvalidResponse
    headers = {}
    for line in lines[1:]:
        name, separator, value = line.partition(':')
        if not separator or not name or any(
                char not in "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                for char in name):
            raise _InvalidResponse
        name = name.lower()
        if name in headers or any(ord(char) < 32 and char != '\t' for char in value):
            raise _InvalidResponse
        headers[name] = value.strip()
    return int(status_line[1]), headers


def _probe(address, deadline, checked_at, progress):
    hostname, port, authorization = address
    try:
        with socket.create_connection((hostname, port), timeout=_remaining(deadline)) as raw:
            request = f'CONNECT {_TARGET}:443 HTTP/1.1\r\nHost: {_TARGET}:443\r\n'
            if authorization is not None:
                request += 'Proxy-Authorization: Basic ' + authorization + '\r\n'
            _send(raw, (request + '\r\n').encode('ascii'), deadline)
            status, _ = _read_headers(raw, deadline)
            if status != 200:
                return _result(checked_at, 'proxy_rejected')
            context = ssl.create_default_context()
            raw.settimeout(_remaining(deadline))
            with context.wrap_socket(raw, server_hostname=_TARGET) as tls:
                progress['tls_ok'] = True
                key = base64.b64encode(os.urandom(16)).decode('ascii')
                request = (
                    f'GET /ws/chat HTTP/1.1\r\nHost: {_TARGET}\r\n'
                    f'Origin: https://{_TARGET}\r\nUpgrade: websocket\r\n'
                    'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                    f'Sec-WebSocket-Key: {key}\r\n\r\n'
                )
                _send(tls, request.encode('ascii'), deadline)
                status, headers = _read_headers(tls, deadline)
                if status != 101:
                    return _result(checked_at, 'websocket_rejected', True)
                expected = base64.b64encode(hashlib.sha1(
                    (key + _WEBSOCKET_GUID).encode('ascii')).digest()).decode('ascii')
                if (headers.get('sec-websocket-accept') != expected
                        or headers.get('upgrade', '').lower() != 'websocket'
                        or 'upgrade' not in [item.strip().lower() for item in
                                            headers.get('connection', '').split(',')]):
                    return _result(checked_at, 'websocket_invalid', True)
                return _result(checked_at, 'ok', True)
    except (TimeoutError, socket.timeout):
        category = 'timeout'
    except ssl.SSLError:
        category = 'tls_error'
    except _InvalidResponse:
        category = 'invalid_response'
    except OSError:
        category = 'connect_error'
    return _result(checked_at, category, progress['tls_ok'])


async def probe_transport(proxy_url: str, timeout: float = 5.0) -> dict:
    """Check only transport; success does not assert account login or message delivery.

    Socket operations share one deadline. The async deadline also bounds DNS or
    OS calls that do not honor a socket timeout; their worker closes resources
    once the OS call returns. Results never include proxy details or raw errors.
    """
    checked_at = time.time()
    try:
        budget = float(timeout)
        if not math.isfinite(budget) or budget <= 0:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        return _result(checked_at, 'invalid_timeout')
    try:
        address = _proxy_address(proxy_url)
    except (ValueError, UnicodeError):
        return _result(checked_at, 'invalid_proxy')
    deadline = time.monotonic() + budget
    progress = {'tls_ok': False}
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_probe, address, deadline, checked_at, progress), timeout=budget)
    except TimeoutError:
        return _result(checked_at, 'timeout', progress['tls_ok'])
