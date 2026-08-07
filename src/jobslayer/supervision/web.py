from __future__ import annotations

import hmac
import json
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

from jobslayer.adapters.local_decisions import (
    DecisionRecordExistsError,
    DecisionStoreError,
)
from jobslayer.supervision.decision import DecisionError
from jobslayer.supervision.session import (
    DecisionAlreadyRecordedError,
    ReviewSession,
    ReviewSessionError,
    StaleDecisionCardError,
)
from jobslayer.workflow.journal import AuditIntegrityError


class ReviewServerError(RuntimeError):
    """Raised when the local review server is configured unsafely."""


class ReviewHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        session: ReviewSession,
    ):
        self.review_session = session
        self.session_token = secrets.token_urlsafe(32)
        super().__init__(server_address, ReviewRequestHandler)


class ReviewRequestHandler(BaseHTTPRequestHandler):
    server: ReviewHttpServer
    protocol_version = "HTTP/1.1"
    maximum_request_bytes = 65_536

    _ASSETS = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/assets/styles.css": ("styles.css", "text/css; charset=utf-8"),
        "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
    }

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlsplit(self.path).path
        if path == "/api/session":
            try:
                payload = self.server.review_session.snapshot()
            except (ReviewSessionError, DecisionStoreError, AuditIntegrityError) as exc:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            payload["submission_token"] = self.server.session_token
            self._send_json(HTTPStatus.OK, payload)
            return
        asset = self._ASSETS.get(path)
        if asset is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "resource not found")
            return
        name, content_type = asset
        content = resources.files("jobslayer.supervision.ui").joinpath(name).read_bytes()
        self._send_bytes(HTTPStatus.OK, content, content_type)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if urlsplit(self.path).path != "/api/decisions":
            self._send_error_json(HTTPStatus.NOT_FOUND, "resource not found")
            return
        supplied_token = self.headers.get("X-JobSlayer-Session", "")
        if not hmac.compare_digest(supplied_token, self.server.session_token):
            self._send_error_json(
                HTTPStatus.FORBIDDEN, "missing or invalid local session token"
            )
            return
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            self._send_error_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "request body must be JSON"
            )
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid content length")
            return
        if content_length <= 0 or content_length > self.maximum_request_bytes:
            self._send_error_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "request body is empty or exceeds the local limit",
            )
            return
        try:
            raw = self.rfile.read(content_length)
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {
                "selected_option_id",
                "rationale",
            }:
                raise ValueError("request must contain only option and rationale")
            selected_option_id = payload["selected_option_id"]
            rationale = payload["rationale"]
            if not isinstance(selected_option_id, str) or not isinstance(
                rationale, str
            ):
                raise ValueError("option and rationale must be strings")
            decision = self.server.review_session.submit(
                selected_option_id=selected_option_id,
                rationale=rationale,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except DecisionError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except (
            DecisionAlreadyRecordedError,
            DecisionRecordExistsError,
            StaleDecisionCardError,
        ) as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
            return
        except (ReviewSessionError, DecisionStoreError) as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self._send_json(
            HTTPStatus.CREATED,
            {
                "status": "recorded_not_applied",
                "decision": decision.model_dump(mode="json"),
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the local control surface quiet unless it returns an API error."""

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"error": message, "status": status.value})

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        content = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self._send_bytes(status, content, "application/json; charset=utf-8")

    def _send_bytes(
        self,
        status: HTTPStatus,
        content: bytes,
        content_type: str,
    ) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(content)


def create_review_server(
    session: ReviewSession,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ReviewHttpServer:
    """Create a loopback-only review server without starting its event loop."""

    if host not in {"127.0.0.1", "localhost"}:
        raise ReviewServerError(
            "the unauthenticated Phase 0 review UI may only bind to loopback"
        )
    if not 0 <= port <= 65_535:
        raise ReviewServerError("port must be between 0 and 65535")
    return ReviewHttpServer((host, port), session)
