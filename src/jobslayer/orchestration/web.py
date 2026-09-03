"""Authenticated loopback API for collaborative task-plan orchestration."""

from __future__ import annotations

import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import secrets
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import ValidationError

from jobslayer.adapters.local_orchestration import (
    TaskPlanJournalError,
    TaskPlanRevisionConflictError,
)
from jobslayer.adapters.local_identity import RoleBasedAuthorizer
from jobslayer.adapters.local_task_manager_runs import (
    TaskManagerRunJournalError,
    TaskManagerRunRevisionConflictError,
)
from jobslayer.adapters.local_task_manager_coordinator import (
    TaskManagerCoordinatorJournalError,
)
from jobslayer.application.planning_artifacts import (
    PlanningArtifactNotFoundError,
    PlanningArtifactQuery,
    PlanningArtifactQueryError,
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
from jobslayer.application.task_manager import (
    TaskManagerCapabilityUnavailableError,
    TaskManagerService,
)
from jobslayer.application.task_manager_execution import (
    StaleTaskManagerRunRevisionError,
    TaskManagerExecutionAdapterUnavailableError,
    TaskManagerExecutionError,
    TaskManagerExecutionEvidenceError,
    TaskManagerExecutionNodeNotFoundError,
    TaskManagerExecutionNodeNotReadyError,
    TaskManagerExecutionProviderError,
    TaskManagerExecutionService,
    TaskManagerPlanNotFinalizedError,
    TaskManagerRunAlreadyExistsError,
    TaskManagerRunNotFoundError,
)
from jobslayer.application.task_manager_coordinator import (
    TaskManagerCoordinatorBusyError,
    TaskManagerCoordinatorError,
    TaskManagerSerialCoordinator,
)
from jobslayer.identity import (
    AuthenticatedPrincipal,
    AuthorizationAction,
    AuthorizationDeniedError,
    AuthorizationRequest,
    require_authorized,
)
from jobslayer.orchestration import (
    PlanningAgentError,
    TaskPlanEdgeRelation,
    TaskPlanNodeKind,
)
from jobslayer.quick_agent import (
    QuickAgent,
    QuickAgentBusyError,
    QuickAgentError,
    QuickAgentMode,
    QuickAgentUnavailableError,
)
from jobslayer.ui_design import UIDesignQuery


class TaskOrchestrationServerError(RuntimeError):
    """Raised when the local orchestration API is configured unsafely."""


class TaskOrchestrationHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        service: TaskOrchestrationService,
        principal: AuthenticatedPrincipal,
        planning_artifacts: PlanningArtifactQuery | None,
        task_manager_execution: TaskManagerExecutionService | None,
        task_manager_coordinator: TaskManagerSerialCoordinator | None,
        ui_designs: UIDesignQuery | None,
        quick_agent: QuickAgent | None,
    ):
        self.orchestration_service = service
        self.task_manager_service = TaskManagerService(
            service,
            task_manager_execution,
            task_manager_coordinator,
        )
        self.planning_artifacts = planning_artifacts
        self.ui_designs = ui_designs
        self.quick_agent = quick_agent
        self.principal = principal
        self.session_token = secrets.token_urlsafe(32)
        super().__init__(server_address, TaskOrchestrationRequestHandler)

    def server_close(self) -> None:
        if self.quick_agent is not None:
            self.quick_agent.close()
        super().server_close()


class TaskOrchestrationRequestHandler(BaseHTTPRequestHandler):
    server: TaskOrchestrationHttpServer
    protocol_version = "HTTP/1.1"
    maximum_request_bytes = 131_072
    prefix = ("api", "orchestration")
    task_manager_prefix = ("api", "task-manager")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        segments = self._segments()
        try:
            if segments == (*self.task_manager_prefix, "session"):
                authorizer = RoleBasedAuthorizer()
                planning_permitted = authorizer.authorize(
                    AuthorizationRequest(
                        principal=self.server.principal,
                        action=AuthorizationAction.MANAGE_TASK_PLAN,
                    )
                ).permitted
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "schema_version": "1.0",
                        "principal": self.server.principal.model_dump(mode="json"),
                        "capabilities": {
                            "task_planning": planning_permitted,
                            "agent_discussion": planning_permitted,
                            "dag_preview": True,
                            "proposal_decisions": planning_permitted,
                            "finalization": planning_permitted,
                            "multi_task_history": True,
                            "planning_artifact_viewer": (
                                self.server.planning_artifacts is not None
                            ),
                            "semantic_ui_design": self.server.ui_designs is not None,
                            "quick_agent_discussion": (
                                self.server.quick_agent is not None
                                and authorizer.authorize(
                                    AuthorizationRequest(
                                        principal=self.server.principal,
                                        action=AuthorizationAction.USE_QUICK_AGENT,
                                    )
                                ).permitted
                            ),
                            "quick_agent_execution": (
                                self.server.quick_agent is not None
                                and authorizer.authorize(
                                    AuthorizationRequest(
                                        principal=self.server.principal,
                                        action=AuthorizationAction.EXECUTE_QUICK_AGENT,
                                    )
                                ).permitted
                            ),
                            "run_assembly": (
                                planning_permitted
                                and
                                self.server.task_manager_service.execution is not None
                            ),
                            "serial_coordinator": (
                                self.server.task_manager_service.coordinator is not None
                            ),
                            "execution_target_binding": (
                                planning_permitted
                                and
                                self.server.task_manager_service.execution is not None
                                and bool(
                                    self.server.task_manager_service.list_execution_targets()
                                )
                            ),
                            "task_execution": (
                                self.server.task_manager_service.execution is not None
                                and self.server.task_manager_service.execution.adapter_available
                            ),
                            "execution_feedback": (
                                self.server.task_manager_service.execution is not None
                                and (
                                    self.server.task_manager_service.execution.adapter_available
                                    or self.server.task_manager_service.execution.validation_available
                                )
                            ),
                            "node_verification": (
                                self.server.task_manager_service.execution is not None
                                and (
                                    self.server.task_manager_service.execution.adapter_available
                                    or self.server.task_manager_service.execution.validation_available
                                )
                            ),
                            "node_validation": (
                                self.server.task_manager_service.execution is not None
                                and self.server.task_manager_service.execution.validation_available
                            ),
                            "completion_approval": authorizer.authorize(
                                AuthorizationRequest(
                                    principal=self.server.principal,
                                    action=AuthorizationAction.APPLY_DECISION,
                                )
                            ).permitted,
                            "node_review": authorizer.authorize(
                                AuthorizationRequest(
                                    principal=self.server.principal,
                                    action=AuthorizationAction.REVIEW_IMPLEMENTATION,
                                )
                            ).permitted,
                            "source_review": authorizer.authorize(
                                AuthorizationRequest(
                                    principal=self.server.principal,
                                    action=AuthorizationAction.REVIEW_IMPLEMENTATION,
                                )
                            ).permitted,
                            "source_checkpoint_approval": authorizer.authorize(
                                AuthorizationRequest(
                                    principal=self.server.principal,
                                    action=AuthorizationAction.APPLY_DECISION,
                                )
                            ).permitted,
                            "source_checkpoint_integration": (
                                self.server.task_manager_service.execution is not None
                                and self.server.task_manager_service.execution.source_integration_available
                                and authorizer.authorize(
                                    AuthorizationRequest(
                                        principal=self.server.principal,
                                        action=AuthorizationAction.INTEGRATE_SOURCE,
                                    )
                                ).permitted
                            ),
                            "human_action_feedback": (
                                self.server.task_manager_service.execution is not None
                                and authorizer.authorize(
                                    AuthorizationRequest(
                                        principal=self.server.principal,
                                        action=AuthorizationAction.RECORD_DECISION,
                                    )
                                ).permitted
                            ),
                            "human_action_agent": (
                                self.server.task_manager_service.execution is not None
                                and self.server.task_manager_service.execution.human_action_assistant_available
                                and authorizer.authorize(
                                    AuthorizationRequest(
                                        principal=self.server.principal,
                                        action=AuthorizationAction.ASSIST_HUMAN_DECISION,
                                    )
                                ).permitted
                            ),
                        },
                        "agent_adapter": (
                            self.server.orchestration_service.planning_agent.adapter_id
                        ),
                        "submission_token": self.server.session_token,
                    },
                )
                return
            if segments[:2] == self.task_manager_prefix:
                if not self._authorized():
                    return
                if segments == (*self.task_manager_prefix, "ui-design"):
                    if self.server.ui_designs is None:
                        self._send_error_json(
                            HTTPStatus.NOT_FOUND,
                            "semantic UI design registry is not configured",
                        )
                        return
                    active_design = self.server.ui_designs.get_active("task-manager")
                    self._send_json(
                        HTTPStatus.OK,
                        active_design.model_dump(mode="json"),
                    )
                    return
                if segments == (*self.task_manager_prefix, "quick-agent", "capacity"):
                    quick_agent = self.server.quick_agent
                    if quick_agent is None:
                        self._send_error_json(
                            HTTPStatus.NOT_FOUND,
                            "Quick Agent adapter is not configured",
                        )
                        return
                    self._require_action(AuthorizationAction.USE_QUICK_AGENT)
                    self._send_json(
                        HTTPStatus.OK,
                        quick_agent.capacity(
                            force_refresh=(
                                parse_qs(urlsplit(self.path).query).get("refresh")
                                == ["1"]
                            )
                        ).model_dump(mode="json"),
                    )
                    return
                if segments == (*self.task_manager_prefix, "quick-agent", "models"):
                    quick_agent = self.server.quick_agent
                    if quick_agent is None:
                        self._send_error_json(
                            HTTPStatus.NOT_FOUND,
                            "Quick Agent adapter is not configured",
                        )
                        return
                    self._require_action(AuthorizationAction.USE_QUICK_AGENT)
                    self._send_json(
                        HTTPStatus.OK,
                        quick_agent.models(
                            force_refresh=(
                                parse_qs(urlsplit(self.path).query).get("refresh")
                                == ["1"]
                            )
                        ).model_dump(mode="json"),
                    )
                    return
                if segments == (*self.task_manager_prefix, "quick-agent", "session"):
                    quick_agent = self.server.quick_agent
                    if quick_agent is None:
                        self._send_error_json(
                            HTTPStatus.NOT_FOUND,
                            "Quick Agent adapter is not configured",
                        )
                        return
                    self._require_action(AuthorizationAction.USE_QUICK_AGENT)
                    self._send_json(
                        HTTPStatus.OK,
                        quick_agent.snapshot().model_dump(mode="json"),
                    )
                    return
                if segments == (*self.task_manager_prefix, "tasks"):
                    tasks = self.server.task_manager_service.list_tasks()
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "schema_version": "1.0",
                            "tasks": [item.model_dump(mode="json") for item in tasks],
                        },
                    )
                    return
                if segments == (*self.task_manager_prefix, "targets"):
                    targets = self.server.task_manager_service.list_execution_targets()
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "schema_version": "1.0",
                            "targets": [
                                item.model_dump(mode="json") for item in targets
                            ],
                        },
                    )
                    return
                if (
                    len(segments) == 5
                    and segments[:3] == (*self.task_manager_prefix, "tasks")
                    and segments[4] == "artifacts"
                ):
                    query = self.server.planning_artifacts
                    if query is None:
                        self._send_error_json(
                            HTTPStatus.NOT_FOUND,
                            "planning artifact viewer is not configured",
                        )
                        return
                    task_id = segments[3]
                    self.server.task_manager_service.get(task_id)
                    artifacts = query.list_for_plan(task_id)
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "schema_version": "1.0",
                            "task_id": task_id,
                            "artifacts": [
                                item.model_dump(mode="json") for item in artifacts
                            ],
                        },
                    )
                    return
                if (
                    len(segments) == 6
                    and segments[:3] == (*self.task_manager_prefix, "tasks")
                    and segments[4] == "artifacts"
                ):
                    query = self.server.planning_artifacts
                    if query is None:
                        self._send_error_json(
                            HTTPStatus.NOT_FOUND,
                            "planning artifact viewer is not configured",
                        )
                        return
                    task_id = segments[3]
                    self.server.task_manager_service.get(task_id)
                    preview = query.preview(task_id, segments[5])
                    self._send_json(HTTPStatus.OK, preview.model_dump(mode="json"))
                    return
                if (
                    len(segments) == 4
                    and segments[:3] == (*self.task_manager_prefix, "tasks")
                ):
                    detail = self.server.task_manager_service.get(segments[3])
                    self._send_json(HTTPStatus.OK, detail.model_dump(mode="json"))
                    return
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
                            "planning_artifact_viewer": (
                                self.server.planning_artifacts is not None
                            ),
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
                if segments[4] == "artifacts":
                    if not self._authorized():
                        return
                    query = self.server.planning_artifacts
                    if query is None:
                        self._send_error_json(
                            HTTPStatus.NOT_FOUND,
                            "planning artifact viewer is not configured",
                        )
                        return
                    self.server.orchestration_service.get(plan_id)
                    artifacts = query.list_for_plan(plan_id)
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "schema_version": "1.0",
                            "plan_id": plan_id,
                            "artifacts": [
                                item.model_dump(mode="json") for item in artifacts
                            ],
                        },
                    )
                    return
            if (
                len(segments) == 6
                and segments[:3] == (*self.prefix, "plans")
                and segments[4] == "artifacts"
            ):
                if not self._authorized():
                    return
                query = self.server.planning_artifacts
                if query is None:
                    self._send_error_json(
                        HTTPStatus.NOT_FOUND,
                        "planning artifact viewer is not configured",
                    )
                    return
                plan_id = segments[3]
                self.server.orchestration_service.get(plan_id)
                preview = query.preview(plan_id, segments[5])
                self._send_json(
                    HTTPStatus.OK,
                    preview.model_dump(mode="json"),
                )
                return
            if len(segments) == 4 and segments[:3] == (*self.prefix, "plans"):
                record = self.server.orchestration_service.get(segments[3])
                self._send_json(HTTPStatus.OK, record.model_dump(mode="json"))
                return
        except TaskPlanNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
            return
        except TaskManagerRunNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
            return
        except PlanningArtifactNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
            return
        except PlanningArtifactQueryError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        except TaskPlanJournalError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        except TaskManagerRunJournalError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        except TaskManagerCoordinatorJournalError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        except TaskManagerExecutionError as exc:
            self._send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            return
        except AuthorizationDeniedError as exc:
            self._send_error_json(HTTPStatus.FORBIDDEN, str(exc))
            return
        except QuickAgentUnavailableError as exc:
            self._send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            return
        except QuickAgentError as exc:
            self._send_error_json(HTTPStatus.BAD_GATEWAY, str(exc))
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
            if segments[:2] == self.task_manager_prefix:
                status, response = self._mutate_task_manager(
                    method, segments, payload
                )
                self._send_json(status, response)
                return
            self._require_action(AuthorizationAction.MANAGE_TASK_PLAN)
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
            TaskManagerRunNotFoundError,
            TaskManagerExecutionNodeNotFoundError,
        ) as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
            return
        except (
            PendingTaskPlanProposalError,
            ArchivedTaskPlanError,
            IncompleteTaskPlanError,
            StaleTaskPlanRevisionError,
            TaskPlanProposalMismatchError,
            TaskPlanRevisionConflictError,
            TaskManagerPlanNotFinalizedError,
            TaskManagerRunAlreadyExistsError,
            StaleTaskManagerRunRevisionError,
            TaskManagerExecutionNodeNotReadyError,
            TaskManagerRunRevisionConflictError,
            TaskManagerCoordinatorBusyError,
        ) as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
            return
        except AuthorizationDeniedError as exc:
            self._send_error_json(HTTPStatus.FORBIDDEN, str(exc))
            return
        except (
            TaskManagerCapabilityUnavailableError,
            TaskManagerExecutionAdapterUnavailableError,
            QuickAgentUnavailableError,
        ) as exc:
            self._send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            return
        except QuickAgentBusyError as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
            return
        except (
            TaskManagerExecutionEvidenceError,
            TaskManagerExecutionProviderError,
        ) as exc:
            self._send_error_json(HTTPStatus.BAD_GATEWAY, str(exc))
            return
        except TaskManagerExecutionError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except TaskManagerCoordinatorError as exc:
            self._send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
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
        except TaskManagerRunJournalError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        except TaskManagerCoordinatorJournalError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self._send_json(status, record.model_dump(mode="json"))

    def _mutate_task_manager(
        self,
        method: str,
        segments: tuple[str, ...],
        payload: dict[str, Any],
    ) -> tuple[HTTPStatus, dict[str, Any]]:
        service = self.server.task_manager_service
        if (
            method == "POST"
            and len(segments) == 8
            and segments[:3] == (*self.task_manager_prefix, "tasks")
            and segments[4] == "runs"
            and segments[6:] == ("coordinator", "tick")
        ):
            self._keys(payload, required={"expected_run_revision"})
            task_id, run_id = segments[3], segments[5]
            self._require_execution_authority(task_id, run_id)
            detail = service.advance_run(
                task_id,
                run_id,
                expected_run_revision=self._integer(
                    payload, "expected_run_revision"
                ),
            )
            return HTTPStatus.OK, detail.model_dump(mode="json")
        if (
            method == "POST"
            and segments == (*self.task_manager_prefix, "quick-agent", "messages")
        ):
            self._keys(
                payload,
                required={"content", "mode"},
                optional={"model", "reasoning_effort", "service_tier"},
            )
            quick_agent = self.server.quick_agent
            if quick_agent is None:
                raise QuickAgentUnavailableError("Quick Agent adapter is not configured")
            mode = QuickAgentMode(self._string(payload, "mode"))
            self._require_action(
                AuthorizationAction.EXECUTE_QUICK_AGENT
                if mode is QuickAgentMode.EXECUTE
                else AuthorizationAction.USE_QUICK_AGENT
            )
            current = quick_agent.snapshot()
            snapshot = quick_agent.start_turn(
                self._string(payload, "content"),
                mode=mode,
                model=(
                    self._optional_string(payload, "model")
                    if "model" in payload
                    else current.model
                ),
                reasoning_effort=(
                    self._optional_string(payload, "reasoning_effort")
                    if "reasoning_effort" in payload
                    else current.reasoning_effort
                ),
                service_tier=(
                    self._optional_string(payload, "service_tier")
                    if "service_tier" in payload
                    else current.service_tier
                ),
            )
            return HTTPStatus.ACCEPTED, snapshot.model_dump(mode="json")
        if (
            method == "POST"
            and segments == (*self.task_manager_prefix, "quick-agent", "cancel")
        ):
            self._keys(payload, required=set())
            quick_agent = self.server.quick_agent
            if quick_agent is None:
                raise QuickAgentUnavailableError("Quick Agent adapter is not configured")
            self._require_action(AuthorizationAction.USE_QUICK_AGENT)
            return HTTPStatus.OK, quick_agent.cancel().model_dump(mode="json")
        if (
            method == "POST"
            and segments == (*self.task_manager_prefix, "quick-agent", "new-session")
        ):
            self._keys(payload, required=set())
            quick_agent = self.server.quick_agent
            if quick_agent is None:
                raise QuickAgentUnavailableError("Quick Agent adapter is not configured")
            self._require_action(AuthorizationAction.USE_QUICK_AGENT)
            return HTTPStatus.OK, quick_agent.new_session().model_dump(mode="json")
        is_node_command = (
            method == "POST"
            and len(segments) == 9
            and segments[:3] == (*self.task_manager_prefix, "tasks")
            and segments[4] == "runs"
            and segments[6] == "nodes"
        )
        if not is_node_command:
            self._require_action(
                AuthorizationAction.MANAGE_TASK_PLAN,
                task_id=segments[3] if len(segments) > 3 else None,
            )
        if method == "POST" and segments == (*self.task_manager_prefix, "tasks"):
            self._keys(payload, required={"task_description"}, optional={"task_id"})
            detail = service.create(
                self._string(payload, "task_description"),
                task_id=self._optional_string(payload, "task_id"),
            )
            return HTTPStatus.CREATED, detail.model_dump(mode="json")
        if (
            method == "POST"
            and len(segments) == 5
            and segments[:3] == (*self.task_manager_prefix, "tasks")
            and segments[4] == "messages"
        ):
            self._keys(
                payload,
                required={"content", "expected_revision"},
                optional={"selected_node_id"},
            )
            detail = service.discuss(
                segments[3],
                self._string(payload, "content"),
                expected_revision=self._integer(payload, "expected_revision"),
                selected_node_id=self._optional_string(payload, "selected_node_id"),
            )
            return HTTPStatus.OK, detail.model_dump(mode="json")
        if (
            method == "POST"
            and len(segments) == 6
            and segments[:3] == (*self.task_manager_prefix, "tasks")
            and segments[4:] == ("proposal", "apply")
        ):
            self._keys(payload, required={"proposal_id", "expected_revision"})
            detail = service.apply_proposal(
                segments[3],
                self._string(payload, "proposal_id"),
                expected_revision=self._integer(payload, "expected_revision"),
            )
            return HTTPStatus.OK, detail.model_dump(mode="json")
        if (
            method == "POST"
            and len(segments) == 6
            and segments[:3] == (*self.task_manager_prefix, "tasks")
            and segments[4:] == ("proposal", "reject")
        ):
            self._keys(payload, required={"proposal_id", "expected_revision"})
            detail = service.reject_proposal(
                segments[3],
                self._string(payload, "proposal_id"),
                expected_revision=self._integer(payload, "expected_revision"),
            )
            return HTTPStatus.OK, detail.model_dump(mode="json")
        if (
            method == "POST"
            and len(segments) == 5
            and segments[:3] == (*self.task_manager_prefix, "tasks")
            and segments[4] == "target"
        ):
            self._keys(payload, required={"target_id", "expected_revision"})
            detail = service.select_execution_target(
                segments[3],
                self._string(payload, "target_id"),
                expected_revision=self._integer(payload, "expected_revision"),
            )
            return HTTPStatus.OK, detail.model_dump(mode="json")
        if (
            method == "POST"
            and len(segments) == 5
            and segments[:3] == (*self.task_manager_prefix, "tasks")
            and segments[4] == "finalize"
        ):
            self._keys(payload, required={"expected_revision"})
            detail = service.finalize(
                segments[3],
                expected_revision=self._integer(payload, "expected_revision"),
            )
            return HTTPStatus.OK, detail.model_dump(mode="json")
        if (
            method == "POST"
            and len(segments) == 5
            and segments[:3] == (*self.task_manager_prefix, "tasks")
            and segments[4] == "runs"
        ):
            self._keys(
                payload,
                required={"expected_revision"},
                optional={"run_id"},
            )
            detail = service.assemble_run(
                segments[3],
                expected_revision=self._integer(payload, "expected_revision"),
                run_id=self._optional_string(payload, "run_id"),
            )
            return HTTPStatus.CREATED, detail.model_dump(mode="json")
        if (
            method == "POST"
            and len(segments) == 9
            and segments[:3] == (*self.task_manager_prefix, "tasks")
            and segments[4] == "runs"
            and segments[6] == "human-actions"
            and segments[8] in {"feedback", "assistant"}
        ):
            task_id, run_id, guidance_id, command = (
                segments[3],
                segments[5],
                segments[7],
                segments[8],
            )
            if command == "feedback":
                self._keys(
                    payload,
                    required={
                        "expected_plan_revision",
                        "expected_run_revision",
                        "decision_id",
                        "content",
                    },
                )
                self._require_action(
                    AuthorizationAction.RECORD_DECISION,
                    task_id=task_id,
                    run_id=run_id,
                )
                detail = service.record_human_action_feedback(
                    task_id,
                    run_id,
                    guidance_id,
                    decision_id=self._string(payload, "decision_id"),
                    content=self._string(payload, "content"),
                    expected_plan_revision=self._integer(
                        payload, "expected_plan_revision"
                    ),
                    expected_run_revision=self._integer(
                        payload, "expected_run_revision"
                    ),
                )
            else:
                self._keys(
                    payload,
                    required={
                        "expected_plan_revision",
                        "expected_run_revision",
                        "content",
                    },
                )
                self._require_action(
                    AuthorizationAction.ASSIST_HUMAN_DECISION,
                    task_id=task_id,
                    run_id=run_id,
                )
                detail = service.request_human_action_assistance(
                    task_id,
                    run_id,
                    guidance_id,
                    content=self._string(payload, "content"),
                    expected_plan_revision=self._integer(
                        payload, "expected_plan_revision"
                    ),
                    expected_run_revision=self._integer(
                        payload, "expected_run_revision"
                    ),
                )
            return HTTPStatus.OK, detail.model_dump(mode="json")
        if (
            method == "POST"
            and len(segments) == 9
            and segments[:3] == (*self.task_manager_prefix, "tasks")
            and segments[4] == "runs"
            and segments[6] == "nodes"
            and segments[8]
            in {
                "start",
                "retry",
                "observe",
                "confirm-scope",
                "verify",
                "accept-review",
                "review-source",
                "approve-checkpoint",
                "integrate-checkpoint",
                "run-validation",
                "approve-completion",
            }
        ):
            task_id, run_id, node_id, command = (
                segments[3],
                segments[5],
                segments[7],
                segments[8],
            )
            if command == "confirm-scope":
                self._keys(
                    payload,
                    required={"expected_run_revision", "rationale"},
                )
                require_authorized(
                    RoleBasedAuthorizer().authorize(
                        AuthorizationRequest(
                            principal=self.server.principal,
                            action=AuthorizationAction.MANAGE_TASK_PLAN,
                            task_id=task_id,
                            run_id=run_id,
                        )
                    )
                )
            elif command in {"accept-review", "review-source"}:
                self._keys(
                    payload,
                    required={"expected_run_revision", "rationale"},
                    optional={"findings"} if command == "review-source" else set(),
                )
                self._require_action(
                    AuthorizationAction.REVIEW_IMPLEMENTATION,
                    task_id=task_id,
                    run_id=run_id,
                )
            elif command == "approve-checkpoint":
                self._keys(
                    payload,
                    required={"expected_run_revision", "rationale"},
                )
                self._require_action(
                    AuthorizationAction.APPLY_DECISION,
                    task_id=task_id,
                    run_id=run_id,
                )
            elif command == "approve-completion":
                self._keys(
                    payload,
                    required={"expected_run_revision", "rationale"},
                )
                self._require_action(
                    AuthorizationAction.APPLY_DECISION,
                    task_id=task_id,
                    run_id=run_id,
                )
            elif command == "integrate-checkpoint":
                self._keys(payload, required={"expected_run_revision"})
                self._require_action(
                    AuthorizationAction.INTEGRATE_SOURCE,
                    task_id=task_id,
                    run_id=run_id,
                )
            elif command == "run-validation":
                self._keys(payload, required={"expected_run_revision"})
                self._require_execution_authority(task_id, run_id)
            else:
                self._keys(payload, required={"expected_run_revision"})
                self._require_execution_authority(task_id, run_id)
            expected = self._integer(payload, "expected_run_revision")
            if command == "confirm-scope":
                detail = service.confirm_scope_gate(
                    task_id,
                    run_id,
                    node_id,
                    expected_run_revision=expected,
                    rationale=self._string(payload, "rationale"),
                )
            elif command == "observe":
                detail = service.observe_node(
                    task_id,
                    run_id,
                    node_id,
                    expected_run_revision=expected,
                )
            elif command == "verify":
                detail = service.verify_node(
                    task_id,
                    run_id,
                    node_id,
                    expected_run_revision=expected,
                )
            elif command == "accept-review":
                detail = service.accept_node_review(
                    task_id,
                    run_id,
                    node_id,
                    expected_run_revision=expected,
                    rationale=self._string(payload, "rationale"),
                )
            elif command == "review-source":
                detail = service.review_source_node(
                    task_id,
                    run_id,
                    node_id,
                    expected_run_revision=expected,
                    rationale=self._string(payload, "rationale"),
                    findings=self._string_tuple(payload, "findings") or (),
                )
            elif command == "approve-checkpoint":
                detail = service.approve_source_checkpoint(
                    task_id,
                    run_id,
                    node_id,
                    expected_run_revision=expected,
                    rationale=self._string(payload, "rationale"),
                )
            elif command == "integrate-checkpoint":
                detail = service.integrate_source_checkpoint(
                    task_id,
                    run_id,
                    node_id,
                    expected_run_revision=expected,
                )
            elif command == "approve-completion":
                detail = service.approve_completion_gate(
                    task_id,
                    run_id,
                    node_id,
                    expected_run_revision=expected,
                    rationale=self._string(payload, "rationale"),
                )
            elif command == "run-validation":
                detail = service.run_validation_node(
                    task_id,
                    run_id,
                    node_id,
                    expected_run_revision=expected,
                )
            else:
                detail = service.start_node(
                    task_id,
                    run_id,
                    node_id,
                    expected_run_revision=expected,
                    retry=command == "retry",
                )
            return HTTPStatus.OK, detail.model_dump(mode="json")
        raise TaskPlanNotFoundError("TaskManager resource not found")

    def _require_execution_authority(self, task_id: str, run_id: str) -> None:
        self._require_action(
            AuthorizationAction.EXECUTE_TASK,
            task_id=task_id,
            run_id=run_id,
        )

    def _require_action(
        self,
        action: AuthorizationAction,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        require_authorized(
            RoleBasedAuthorizer().authorize(
                AuthorizationRequest(
                    principal=self.server.principal,
                    action=action,
                    task_id=task_id,
                    run_id=run_id,
                )
            )
        )

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
    planning_artifacts: PlanningArtifactQuery | None = None,
    task_manager_execution: TaskManagerExecutionService | None = None,
    task_manager_coordinator: TaskManagerSerialCoordinator | None = None,
    ui_designs: UIDesignQuery | None = None,
    quick_agent: QuickAgent | None = None,
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
        return TaskOrchestrationHttpServer(
            (host, port),
            service,
            principal,
            planning_artifacts,
            task_manager_execution,
            task_manager_coordinator,
            ui_designs,
            quick_agent,
        )
    except OSError as exc:
        raise TaskOrchestrationServerError(
            "task-orchestration server could not bind"
        ) from exc


__all__ = [
    "TaskOrchestrationHttpServer",
    "TaskOrchestrationServerError",
    "create_task_orchestration_server",
]
