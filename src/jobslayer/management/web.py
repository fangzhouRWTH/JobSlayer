"""Loopback-only authenticated read surface for Agent development management."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import ipaddress
import json
from urllib.parse import unquote, urlsplit

from jobslayer.identity import AuthenticatedPrincipal
from jobslayer.management import ManagementQuery, ManagementQueryError


class ManagementServerError(RuntimeError):
    pass


class ManagementHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address, handler, *, query, principal):
        self.query = query
        self.principal = principal
        super().__init__(server_address, handler)


class _Handler(BaseHTTPRequestHandler):
    server: ManagementHttpServer

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == "/api/session":
            self._json(
                {
                    "schema_version": "1.0",
                    "principal": self.server.principal.model_dump(mode="json"),
                    "capabilities": {
                        "view": True,
                        "mutations": False,
                        "live_source": getattr(
                            self.server.query,
                            "source_kind",
                            "persisted_events",
                        ),
                    },
                }
            )
            return
        if route == "/api/dashboard":
            try:
                snapshot = self.server.query.snapshot()
            except ManagementQueryError as exc:
                self._error(HTTPStatus.CONFLICT, str(exc))
                return
            self._json(snapshot.model_dump(mode="json"))
            return
        prefix = "/api/runs/"
        if route.startswith(prefix):
            run_id = unquote(route[len(prefix) :])
            try:
                detail = self.server.query.run_detail(run_id)
            except ManagementQueryError as exc:
                self._error(HTTPStatus.NOT_FOUND, str(exc))
                return
            self._json(detail)
            return
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        selected = assets.get(route)
        if selected is None:
            self._error(HTTPStatus.NOT_FOUND, "resource not found")
            return
        name, content_type = selected
        content = resources.files("jobslayer.management.ui").joinpath(name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self._security_headers(content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "dashboard is read-only")

    def log_message(self, format: str, *args) -> None:
        return

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        self.send_response(status)
        self._security_headers("application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _security_headers(self, content_type: str) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'none'",
        )


def create_management_server(
    query: ManagementQuery,
    principal: AuthenticatedPrincipal,
    *,
    host: str = "127.0.0.1",
    port: int = 8770,
) -> ManagementHttpServer:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ManagementServerError("management host must be a loopback IP") from exc
    if not address.is_loopback:
        raise ManagementServerError("management server may only bind to loopback")
    if port < 0 or port > 65535:
        raise ManagementServerError("management port is invalid")
    try:
        return ManagementHttpServer(
            (host, port),
            _Handler,
            query=query,
            principal=principal,
        )
    except OSError as exc:
        raise ManagementServerError("management server could not bind") from exc


__all__ = ["ManagementServerError", "create_management_server"]
