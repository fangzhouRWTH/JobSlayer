"""Authenticated loopback API for collaborative task-plan orchestration."""

from __future__ import annotations

import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import secrets
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError

from jobslayer.adapters.local_orchestration import (
    TaskPlanJournalError,
    TaskPlanRevisionConflictError,
)
from jobslayer.application.task_orchestration import (
    ArchivedTaskPlanError,
    IncompleteTaskPlanError,
    PendingTaskPlanProposalError,
    StaleTaskPlanRevisionError,
    TaskOrchestrationError,
    TaskOrchestrationService,
    TaskPlanNotFoundError,
    TaskPlanProposalMismatchError,
)
from jobslayer.identity import AuthenticatedPrincipal
from jobslayer.orchestration import (
    PlanningAgentError,
    TaskPlanEdgeRelation,
    TaskPlanNodeKind,
)


class TaskOrchestrationServerError(RuntimeError):
    """Raised when the local orchestration API is configured unsafely."""


class TaskOrchestrationHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        service: TaskOrchestrationService,
        principal: AuthenticatedPrincipal,
    ):
        self.orchestration_service = service
        self.principal = principal
        self.session_token = secrets.token_urlsafe(32)
        super().__init__(server_address, TaskOrchestrationRequestHandler)


class TaskOrchestrationRequestHandler(BaseHTTPRequestHandler):
    server: TaskOrchestrationHttpServer
    protocol_version = "HTTP/1.1"
    maximum_request_bytes = 131_072
    prefix = ("api", "orchestration")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        segments = self._segments()
        try:
            if segments == (*self.prefix, "session"):
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "schema_version": "1.0",
                        "principal": self.server.principal.model_dump(mode="json"),
                        "capabilities": {
                            "discussion": True,
                            "agent_proposals": True,
                            "node_crud": True,
                            "edge_crud": True,
                            "branch_and_subtask": True,
                            "proposal_rejection": True,
                            "revision_derivation": True,
                            "plan_archiving": True,
                            "completeness_assessment": True,
                            "finalization": True,
                            "workflow_execution": False,
                        },
                        "agent_adapter": (
                            self.server.orchestration_service.planning_agent.adapter_id
                        ),
                        "submission_token": self.server.session_token,
                    },
                )
                return
            if segments == (*self.prefix, "plans"):
                records = self.server.orchestration_service.list_latest()
                self._send_json(
                    HTTPStatus.OK,
                    {"plans": [record.model_dump(mode="json") for record in records]},
                )
                return
            if len(segments) == 5 and segments[:3] == (*self.prefix, "plans"):
                plan_id = segments[3]
                if segments[4] == "history":
                    records = self.server.orchestration_service.history(plan_id)
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "plan_id": plan_id,
                            "history": [
                                record.model_dump(mode="json") for record in records
                            ],
                        },
                    )
                    return
                if segments[4] == "assessment":
                    assessment = self.server.orchestration_service.assess(plan_id)
                    self._send_json(
                        HTTPStatus.OK,
                        assessment.model_dump(mode="json"),
                    )
                    return
            if len(segments) == 4 and segments[:3] == (*self.prefix, "plans"):
                record = self.server.orchestration_service.get(segments[3])
                self._send_json(HTTPStatus.OK, record.model_dump(mode="json"))
                return
        except TaskPlanNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
            return
        except TaskPlanJournalError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "resource not found")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._mutate("POST")

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._mutate("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._mutate("DELETE")

    def _mutate(self, method: str) -> None:
        if not self._authorized():
            return
        try:
            payload = self._read_json()
            segments = self._segments()
            service = self.server.orchestration_service
            status = HTTPStatus.OK
            if method == "POST" and segments == (*self.prefix, "plans"):
                self._keys(payload, required={"task_description"}, optional={"plan_id"})
                record = service.create(
                    self._string(payload, "task_description"),
                    plan_id=self._optional_string(payload, "plan_id"),
                )
                status = HTTPStatus.CREATED
            elif (
                method == "POST"
                and len(segments) == 5
                and segments[:3] == (*self.prefix, "plans")
                and segments[4] == "messages"
            ):
                self._keys(
                    payload,
                    required={"content", "expected_revision"},
                    optional={"selected_node_id"},
                )
                record = service.discuss(
                    segments[3],
                    self._string(payload, "content"),
                    expected_revision=self._integer(payload, "expected_revision"),
                    selected_node_id=self._optional_string(
                        payload, "selected_node_id"
                    ),
                )
            elif (
                method == "POST"
                and len(segments) == 6
                and segments[:3] == (*self.prefix, "plans")
                and segments[4:] == ("proposals", "apply")
            ):
                self._keys(
                    payload, required={"proposal_id", "expected_revision"}
                )
                record = service.apply_proposal(
                    segments[3],
                    self._string(payload, "proposal_id"),
                    expected_revision=self._integer(payload, "expected_revision"),
                )
            elif (
                method == "POST"
                and len(segments) == 6
                and segments[:3] == (*self.prefix, "plans")
                and segments[4:] == ("proposals", "reject")
            ):
                self._keys(
                    payload, required={"proposal_id", "expected_revision"}
                )
                record = service.reject_proposal(
                    segments[3],
                    self._string(payload, "proposal_id"),
                    expected_revision=self._integer(payload, "expected_revision"),
                )
            elif (
                method == "POST"
                and len(segments) == 5
                and segments[:3] == (*self.prefix, "plans")
                and segments[4] == "nodes"
            ):
                self._keys(
                    payload,
                    required={"title", "expected_revision"},
                    optional={
                        "description",
                        "kind",
                        "executor_hint",
                        "source_node_id",
                        "relation",
                        "node_id",
                        "acceptance_criteria",
                        "deliverables",
                        "constraints",
                        "risks",
                        "verification_requirements",
                        "requires_human_decision",
                    },
                )
                record = service.create_node(
                    segments[3],
                    expected_revision=self._integer(payload, "expected_revision"),
                    title=self._string(payload, "title"),
                    description=self._optional_string(payload, "description") or "",
                    kind=TaskPlanNodeKind(payload.get("kind", "task")),
                    executor_hint=self._optional_string(payload, "executor_hint"),
                    source_node_id=self._optional_string(payload, "source_node_id"),
                    relation=TaskPlanEdgeRelation(payload.get("relation", "sequence")),
                    node_id=self._optional_string(payload, "node_id"),
                    acceptance_criteria=(
                        self._string_tuple(payload, "acceptance_criteria") or ()
                    ),
                    deliverables=self._string_tuple(payload, "deliverables") or (),
                    constraints=self._string_tuple(payload, "constraints") or (),
                    risks=self._string_tuple(payload, "risks") or (),
                    verification_requirements=(
                        self._string_tuple(payload, "verification_requirements") or ()
                    ),
                    requires_human_decision=(
                        self._boolean(payload, "requires_human_decision")
                        if "requires_human_decision" in payload
                        else False
                    ),
                )
                status = HTTPStatus.CREATED
            elif (
                method == "POST"
                and len(segments) == 5
                and segments[:3] == (*self.prefix, "plans")
                and segments[4] == "edges"
            ):
                self._keys(
                    payload,
                    required={
                        "source_node_id",
                        "target_node_id",
                        "relation",
                        "expected_revision",
                    },
                    optional={"label", "edge_id"},
                )
                record = service.create_edge(
                    segments[3],
                    expected_revision=self._integer(payload, "expected_revision"),
                    source_node_id=self._string(payload, "source_node_id"),
                    target_node_id=self._string(payload, "target_node_id"),
                    relation=TaskPlanEdgeRelation(self._string(payload, "relation")),
                    label=self._optional_string(payload, "label"),
                    edge_id=self._optional_string(payload, "edge_id"),
                )
                status = HTTPStatus.CREATED
            elif (
                method == "POST"
                and len(segments) == 7
                and segments[:3] == (*self.prefix, "plans")
                and segments[4] == "nodes"
                and segments[6] == "split"
            ):
                self._keys(
                    payload,
                    required={
                        "title",
                        "description",
                        "relation",
                        "expected_revision",
                    },
                )
                record = service.split_node(
                    segments[3],
                    segments[5],
                    expected_revision=self._integer(payload, "expected_revision"),
                    title=self._string(payload, "title"),
                    description=self._string(payload, "description", allow_blank=True),
                    relation=TaskPlanEdgeRelation(
                        self._string(payload, "relation")
                    ),
                )
                status = HTTPStatus.CREATED
            elif (
                method == "POST"
                and len(segments) == 5
                and segments[:3] == (*self.prefix, "plans")
                and segments[4] == "finalize"
            ):
                self._keys(payload, required={"expected_revision"})
                record = service.finalize(
                    segments[3],
                    expected_revision=self._integer(payload, "expected_revision"),
                )
            elif (
                method == "POST"
                and len(segments) == 7
                and segments[:3] == (*self.prefix, "plans")
                and segments[4] == "revisions"
                and segments[6] == "derive"
            ):
                self._keys(payload, required={"expected_revision"})
                record = service.derive_from_revision(
                    segments[3],
                    self._path_integer(segments[5], "source revision"),
                    expected_revision=self._integer(payload, "expected_revision"),
                )
            elif (
                method == "POST"
                and len(segments) == 5
                and segments[:3] == (*self.prefix, "plans")
                and segments[4] == "archive"
            ):
                self._keys(payload, required={"archived", "expected_revision"})
                record = service.set_archived(
                    segments[3],
                    archived=self._boolean(payload, "archived"),
                    expected_revision=self._integer(payload, "expected_revision"),
                )
            elif (
                method == "PATCH"
                and len(segments) == 6
                and segments[:3] == (*self.prefix, "plans")
                and segments[4] == "nodes"
            ):
                self._keys(
                    payload,
                    required={
                        "title",
                        "description",
                        "kind",
                        "executor_hint",
                        "expected_revision",
                    },
                    optional={
                        "acceptance_criteria",
                        "deliverables",
                        "constraints",
                        "risks",
                        "verification_requirements",
                        "requires_human_decision",
                    },
                )
                record = service.update_node(
                    segments[3],
                    segments[5],
                    expected_revision=self._integer(payload, "expected_revision"),
                    title=self._string(payload, "title"),
                    description=self._string(payload, "description", allow_blank=True),
                    kind=TaskPlanNodeKind(self._string(payload, "kind")),
                    executor_hint=self._optional_string(payload, "executor_hint"),
                    acceptance_criteria=self._string_tuple(
                        payload, "acceptance_criteria"
                    ),
                    deliverables=self._string_tuple(payload, "deliverables"),
                    constraints=self._string_tuple(payload, "constraints"),
                    risks=self._string_tuple(payload, "risks"),
                    verification_requirements=self._string_tuple(
                        payload, "verification_requirements"
                    ),
                    requires_human_decision=(
                        self._boolean(payload, "requires_human_decision")
                        if "requires_human_decision" in payload
                        else None
                    ),
                )
            elif (
                method == "PATCH"
                and len(segments) == 6
                and segments[:3] == (*self.prefix, "plans")
                and segments[4] == "edges"
            ):
                self._keys(
                    payload,
                    required={"relation", "label", "expected_revision"},
                )
                record = service.update_edge(
                    segments[3],
                    segments[5],
                    expected_revision=self._integer(payload, "expected_revision"),
                    relation=TaskPlanEdgeRelation(self._string(payload, "relation")),
                    label=self._optional_string(payload, "label"),
                )
            elif (
                method == "DELETE"
                and len(segments) == 6
                and segments[:3] == (*self.prefix, "plans")
                and segments[4] == "nodes"
            ):
                self._keys(payload, required={"expected_revision"})
                record = service.delete_node(
                    segments[3],
                    segments[5],
                    expected_revision=self._integer(payload, "expected_revision"),
                )
            elif (
                method == "DELETE"
                and len(segments) == 6
                and segments[:3] == (*self.prefix, "plans")
                and segments[4] == "edges"
            ):
                self._keys(payload, required={"expected_revision"})
                record = service.delete_edge(
                    segments[3],
                    segments[5],
                    expected_revision=self._integer(payload, "expected_revision"),
                )
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, "resource not found")
                return
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except TaskPlanNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
            return
        except (
            PendingTaskPlanProposalError,
            ArchivedTaskPlanError,
            IncompleteTaskPlanError,
            StaleTaskPlanRevisionError,
            TaskPlanProposalMismatchError,
            TaskPlanRevisionConflictError,
        ) as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
            return
        except TaskOrchestrationError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except PlanningAgentError as exc:
            self._send_error_json(HTTPStatus.BAD_GATEWAY, str(exc))
            return
        except TaskPlanJournalError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self._send_json(status, record.model_dump(mode="json"))

    def _segments(self) -> tuple[str, ...]:
        path = urlsplit(self.path).path
        return tuple(unquote(item) for item in path.strip("/").split("/") if item)

    def _authorized(self) -> bool:
        if not self.server.principal.is_active():
            self._send_error_json(
                HTTPStatus.FORBIDDEN, "authenticated planner session has expired"
            )
            return False
        supplied_token = self.headers.get("X-JobSlayer-Session", "")
        if not hmac.compare_digest(supplied_token, self.server.session_token):
            self._send_error_json(
                HTTPStatus.FORBIDDEN, "missing or invalid local session token"
            )
            return False
        return True

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            raise ValueError("request body must be JSON")
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if content_length <= 0 or content_length > self.maximum_request_bytes:
            raise ValueError("request body is empty or exceeds the local limit")
        payload = json.loads(self.rfile.read(content_length))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    @staticmethod
    def _keys(
        payload: dict[str, Any],
        *,
        required: set[str],
        optional: set[str] | None = None,
    ) -> None:
        optional = optional or set()
        if not required.issubset(payload) or not set(payload).issubset(
            required | optional
        ):
            raise ValueError(
                "request fields do not match the orchestration command contract"
            )

    @staticmethod
    def _string(
        payload: dict[str, Any], name: str, *, allow_blank: bool = False
    ) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or (not allow_blank and not value.strip()):
            raise ValueError(f"{name} must be a string")
        return value

    @staticmethod
    def _optional_string(payload: dict[str, Any], name: str) -> str | None:
        value = payload.get(name)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be null or a non-blank string")
        return value

    @staticmethod
    def _integer(payload: dict[str, Any], name: str) -> int:
        value = payload.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _path_integer(value: str, name: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be a positive integer") from exc
        if parsed < 1:
            raise ValueError(f"{name} must be a positive integer")
        return parsed

    @staticmethod
    def _boolean(payload: dict[str, Any], name: str) -> bool:
        value = payload.get(name)
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
        return value

    @staticmethod
    def _string_tuple(
        payload: dict[str, Any], name: str
    ) -> tuple[str, ...] | None:
        value = payload.get(name)
        if value is None:
            return None
        if (
            not isinstance(value, list)
            or len(value) > 24
            or any(
                not isinstance(item, str)
                or not item.strip()
                or len(item) > 1_000
                for item in value
            )
        ):
            raise ValueError(f"{name} must be a bounded list of non-blank strings")
        return tuple(value)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"error": message, "status": status.value})

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        content = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)


def create_task_orchestration_server(
    service: TaskOrchestrationService,
    principal: AuthenticatedPrincipal,
    *,
    host: str = "127.0.0.1",
    port: int = 8780,
) -> TaskOrchestrationHttpServer:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise TaskOrchestrationServerError(
            "task-orchestration host must be a loopback IP"
        ) from exc
    if not address.is_loopback:
        raise TaskOrchestrationServerError(
            "task-orchestration API may only bind to loopback"
        )
    if port < 0 or port > 65_535:
        raise TaskOrchestrationServerError("task-orchestration port is invalid")
    try:
        return TaskOrchestrationHttpServer((host, port), service, principal)
    except OSError as exc:
        raise TaskOrchestrationServerError(
            "task-orchestration server could not bind"
        ) from exc


__all__ = [
    "TaskOrchestrationHttpServer",
    "TaskOrchestrationServerError",
    "create_task_orchestration_server",
]
