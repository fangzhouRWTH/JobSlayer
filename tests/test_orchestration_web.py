from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.adapters.local_human_action_assistant import LocalHumanActionAssistant
from jobslayer.adapters.local_orchestration import LocalTaskPlanStore
from jobslayer.adapters.local_planning_agent import LocalPlanningAgent
from jobslayer.adapters.local_task_manager_coordinator import (
    LocalTaskManagerCoordinatorStore,
)
from jobslayer.adapters.local_task_manager_runs import LocalTaskManagerRunStore
from jobslayer.adapters.sqlite_workers import SqliteWorkerLeaseStore
from jobslayer.adapters.source_ui_designs import SourceControlledUIDesignRegistry
from jobslayer.application.planning_artifacts import PlanningArtifactQuery
from jobslayer.application.task_manager_coordinator import TaskManagerSerialCoordinator
from jobslayer.application.task_manager_execution import TaskManagerExecutionService
from jobslayer.application.task_orchestration import TaskOrchestrationService
from jobslayer.identity import AuthenticatedPrincipal, AuthenticationMethod
from jobslayer.orchestration import PlanningAgentError, TaskPlanNodeKind
from jobslayer.quick_agent import (
    QuickAgentCapacitySnapshot,
    QuickAgentEvent,
    QuickAgentMode,
    QuickAgentModelCatalogSnapshot,
    QuickAgentModelOption,
    QuickAgentRateLimitBucket,
    QuickAgentRateLimitWindow,
    QuickAgentReasoningEffortOption,
    QuickAgentSessionSnapshot,
    QuickAgentServiceTierOption,
    QuickAgentState,
)
from jobslayer.orchestration.web import (
    TaskOrchestrationServerError,
    create_task_orchestration_server,
)
from tests.task_manager_fixtures import FixtureExecutionTargetRegistry
from tests.test_task_manager_execution import FixtureManagedExecutor


class FixtureQuickAgent:
    adapter_id = "fixture-quick-agent"

    def __init__(self) -> None:
        self.force_refreshes: list[bool] = []
        self.model_force_refreshes: list[bool] = []
        self.started: list[tuple[str, QuickAgentMode, str, str, str | None]] = []
        self.cancelled = 0
        self.reset = 0
        self.closed = False

    def capacity(self, *, force_refresh: bool = False) -> QuickAgentCapacitySnapshot:
        self.force_refreshes.append(force_refresh)
        now = datetime.now(UTC)
        return QuickAgentCapacitySnapshot(
            available=True,
            source="fixture.account/rateLimits/read",
            observed_at=now,
            refresh_after_seconds=30,
            buckets=(
                QuickAgentRateLimitBucket(
                    limit_id="codex",
                    limit_name="Codex",
                    plan_type="pro",
                    primary=QuickAgentRateLimitWindow(
                        used_percent=25,
                        remaining_percent=75,
                        window_duration_minutes=300,
                        resets_at=now + timedelta(hours=2),
                    ),
                ),
            ),
        )

    def models(self, *, force_refresh: bool = False) -> QuickAgentModelCatalogSnapshot:
        self.model_force_refreshes.append(force_refresh)
        return QuickAgentModelCatalogSnapshot(
            available=True,
            source="fixture.model/list",
            observed_at=datetime.now(UTC),
            refresh_after_seconds=300,
            models=(
                QuickAgentModelOption(
                    model_id="gpt-5.6-sol",
                    display_name="GPT-5.6-Sol",
                    description="Reliable agentic workhorse.",
                    default_reasoning_effort="low",
                    reasoning_efforts=(
                        QuickAgentReasoningEffortOption(
                            effort="low", description="Faster"
                        ),
                        QuickAgentReasoningEffortOption(
                            effort="xhigh", description="Deeper"
                        ),
                    ),
                    input_modalities=("text", "image"),
                    multi_agent_version="v2",
                    service_tiers=(
                        QuickAgentServiceTierOption(
                            tier_id="priority",
                            name="Fast",
                            description="1.5x speed",
                        ),
                    ),
                    is_default=True,
                ),
            ),
        )

    def snapshot(self) -> QuickAgentSessionSnapshot:
        now = datetime.now(UTC)
        events = tuple(
            QuickAgentEvent(
                sequence=index,
                event_type="user.message",
                role="user",
                content=content,
                created_at=now,
                mode=mode,
                status="submitted",
            )
            for index, (content, mode, _model, _effort, _tier) in enumerate(
                self.started, start=1
            )
        )
        return QuickAgentSessionSnapshot(
            adapter_id=self.adapter_id,
            conversation_id="quick-fixture",
            state=QuickAgentState.IDLE,
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
            service_tier=(self.started[-1][4] if self.started else None),
            workspace_root="/fixture",
            maximum_turn_seconds=1800,
            events=events,
            updated_at=now,
        )

    def start_turn(
        self,
        content: str,
        *,
        mode: QuickAgentMode,
        model: str | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
    ) -> QuickAgentSessionSnapshot:
        self.started.append(
            (
                content,
                mode,
                model or "gpt-5.6-sol",
                reasoning_effort or "xhigh",
                service_tier,
            )
        )
        return self.snapshot()

    def cancel(self) -> QuickAgentSessionSnapshot:
        self.cancelled += 1
        return self.snapshot()

    def new_session(self) -> QuickAgentSessionSnapshot:
        self.reset += 1
        self.started.clear()
        return self.snapshot()

    def close(self) -> None:
        self.closed = True


class TaskOrchestrationWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.root = root
        now = datetime.now(UTC)
        self.principal = AuthenticatedPrincipal(
            session_id="session-planner",
            subject_id="planner@example.invalid",
            display_name="Task Planner",
            roles=("planner",),
            authentication_method=AuthenticationMethod.LOCAL_SIGNED_SESSION,
            issuer="test-issuer",
            authenticated_at=now,
            valid_until=now + timedelta(minutes=5),
        )
        service = TaskOrchestrationService(
            LocalTaskPlanStore(root),
            LocalPlanningAgent(),
            actor_id=self.principal.subject_id,
        )
        self.artifacts = LocalArtifactRegistry(root / "planning-artifacts")
        self.task_manager_execution = TaskManagerExecutionService(
            LocalTaskManagerRunStore(root / "task-manager-runs"),
            self.artifacts,
            actor_id=self.principal.subject_id,
            targets=FixtureExecutionTargetRegistry(),
        )
        self.server = create_task_orchestration_server(
            service,
            self.principal,
            planning_artifacts=PlanningArtifactQuery(
                self.artifacts, max_preview_bytes=1_024
            ),
            task_manager_execution=self.task_manager_execution,
            ui_designs=SourceControlledUIDesignRegistry(
                Path(__file__).resolve().parents[1] / "ui-designs" / "catalog.json"
            ),
            port=0,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}/api/orchestration"
        self.task_manager_base = f"http://{host}:{port}/api/task-manager"
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def get_json(
        self,
        path: str,
        *,
        token: str | None = None,
        base: str | None = None,
    ) -> dict:
        headers = {}
        if token is not None:
            headers["X-JobSlayer-Session"] = token
        request = Request((base or self.base) + path, headers=headers, method="GET")
        with self.opener.open(request, timeout=2) as response:
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            return json.loads(response.read())

    def mutate(
        self,
        method: str,
        path: str,
        payload: dict,
        *,
        token: str | None,
        base: str | None = None,
    ) -> dict:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-JobSlayer-Session"] = token
        request = Request(
            (base or self.base) + path,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method=method,
        )
        with self.opener.open(request, timeout=2) as response:
            return json.loads(response.read())

    def test_task_manager_api_exposes_focused_authenticated_planning_loop(self) -> None:
        session = self.get_json("/session", base=self.task_manager_base)
        self.assertTrue(session["capabilities"]["task_planning"])
        self.assertTrue(session["capabilities"]["run_assembly"])
        self.assertFalse(session["capabilities"]["serial_coordinator"])
        self.assertTrue(session["capabilities"]["execution_target_binding"])
        self.assertFalse(session["capabilities"]["task_execution"])
        self.assertFalse(session["capabilities"]["node_verification"])
        self.assertFalse(session["capabilities"]["node_validation"])
        self.assertFalse(session["capabilities"]["completion_approval"])
        self.assertFalse(session["capabilities"]["node_review"])
        self.assertFalse(session["capabilities"]["source_review"])
        self.assertFalse(session["capabilities"]["source_checkpoint_approval"])
        self.assertFalse(session["capabilities"]["source_checkpoint_integration"])
        self.assertTrue(session["capabilities"]["semantic_ui_design"])
        self.assertFalse(session["capabilities"]["quick_agent_discussion"])
        self.assertFalse(session["capabilities"]["quick_agent_execution"])
        self.assertFalse(session["capabilities"]["human_action_feedback"])
        self.assertFalse(session["capabilities"]["human_action_agent"])
        token = session["submission_token"]

        ui_design = self.get_json(
            "/ui-design", token=token, base=self.task_manager_base
        )
        self.assertEqual(ui_design["binding"]["scheme_id"], "focused-task-graph")
        self.assertEqual(ui_design["binding"]["revision"], 12)
        self.assertEqual(ui_design["state_counts"]["planned"], 73)

        targets = self.get_json(
            "/targets", token=token, base=self.task_manager_base
        )
        self.assertEqual(targets["targets"][0]["target_id"], "fixture-project-target-v1")

        with self.assertRaises(HTTPError) as unauthorized:
            self.get_json("/tasks", base=self.task_manager_base)
        self.assertEqual(unauthorized.exception.code, 403)

        created = self.mutate(
            "POST",
            "/tasks",
            {
                "task_description": "开发 TaskManager 可视化闭环",
                "task_id": "task-manager-web",
            },
            token=token,
            base=self.task_manager_base,
        )
        self.assertEqual(created["task"]["stage"], "proposal_pending")
        self.assertFalse(created["execution_available"])
        self.assertTrue(created["execution_blockers"])

        proposal = created["plan"]["pending_proposal"]
        applied = self.mutate(
            "POST",
            "/tasks/task-manager-web/proposal/apply",
            {
                "proposal_id": proposal["proposal_id"],
                "expected_revision": created["task"]["revision"],
            },
            token=token,
            base=self.task_manager_base,
        )
        targeted = self.mutate(
            "POST",
            "/tasks/task-manager-web/target",
            {
                "target_id": "fixture-project-target-v1",
                "expected_revision": applied["task"]["revision"],
            },
            token=token,
            base=self.task_manager_base,
        )
        self.assertTrue(targeted["execution_target_assessment"]["ready"])
        registry = self.task_manager_execution.targets
        self.assertIsInstance(registry, FixtureExecutionTargetRegistry)
        assert isinstance(registry, FixtureExecutionTargetRegistry)
        registry.binding = registry.binding.model_copy(
            update={"source_bundle_sha256": "c" * 64}
        )
        drifted = self.get_json(
            "/tasks/task-manager-web",
            token=token,
            base=self.task_manager_base,
        )
        self.assertFalse(drifted["execution_target_assessment"]["ready"])
        self.assertIn(
            "target.source_binding_drift",
            {
                issue["code"]
                for issue in drifted["execution_target_assessment"]["issues"]
            },
        )
        targeted = self.mutate(
            "POST",
            "/tasks/task-manager-web/target",
            {
                "target_id": "fixture-project-target-v1",
                "expected_revision": targeted["task"]["revision"],
            },
            token=token,
            base=self.task_manager_base,
        )
        self.assertTrue(targeted["execution_target_assessment"]["ready"])
        self.assertEqual(
            targeted["plan"]["execution_target_source_sha256"], "c" * 64
        )
        finalized = self.mutate(
            "POST",
            "/tasks/task-manager-web/finalize",
            {"expected_revision": targeted["task"]["revision"]},
            token=token,
            base=self.task_manager_base,
        )
        self.assertEqual(finalized["task"]["stage"], "ready")
        self.assertTrue(all(item["state"] == "ready" for item in finalized["nodes"]))
        self.assertTrue(finalized["run_assembly_available"])

        assembled = self.mutate(
            "POST",
            "/tasks/task-manager-web/runs",
            {
                "expected_revision": finalized["task"]["revision"],
                "run_id": "tmrun-manager-web",
            },
            token=token,
            base=self.task_manager_base,
        )
        self.assertEqual(assembled["execution_run"]["revision"], 1)
        self.assertEqual(assembled["execution_run"]["stage"], "ready")
        self.assertEqual(assembled["nodes"][0]["state"], "ready")
        self.assertTrue(
            all(item["state"] == "waiting" for item in assembled["nodes"][1:])
        )
        self.assertFalse(assembled["run_assembly_available"])
        self.assertFalse(assembled["execution_available"])

        with self.assertRaises(HTTPError) as assembled_run_locks_planning:
            self.mutate(
                "POST",
                "/tasks/task-manager-web/messages",
                {
                    "content": "this must become a separate task",
                    "expected_revision": finalized["task"]["revision"],
                },
                token=token,
                base=self.task_manager_base,
            )
        self.assertEqual(assembled_run_locks_planning.exception.code, 409)
        locked_detail = self.get_json(
            "/tasks/task-manager-web",
            token=token,
            base=self.task_manager_base,
        )
        self.assertEqual(
            locked_detail["task"]["record_hash"], finalized["task"]["record_hash"]
        )
        self.assertEqual(locked_detail["execution_run"]["run_id"], "tmrun-manager-web")

        with self.assertRaises(HTTPError) as planner_cannot_execute:
            self.mutate(
                "POST",
                "/tasks/task-manager-web/runs/tmrun-manager-web/nodes/scope/start",
                {"expected_run_revision": 1},
                token=token,
                base=self.task_manager_base,
            )
        self.assertEqual(planner_cannot_execute.exception.code, 403)

        with self.assertRaises(HTTPError) as planner_cannot_review:
            self.mutate(
                "POST",
                "/tasks/task-manager-web/runs/tmrun-manager-web/nodes/scope/accept-review",
                {
                    "expected_run_revision": 1,
                    "rationale": "planner role is not reviewer authority",
                },
                token=token,
                base=self.task_manager_base,
            )
        self.assertEqual(planner_cannot_review.exception.code, 403)

        with self.assertRaises(HTTPError) as planner_cannot_validate:
            self.mutate(
                "POST",
                "/tasks/task-manager-web/runs/tmrun-manager-web/"
                "nodes/verify/run-validation",
                {"expected_run_revision": 1},
                token=token,
                base=self.task_manager_base,
            )
        self.assertEqual(planner_cannot_validate.exception.code, 403)

        with self.assertRaises(HTTPError) as planner_cannot_approve_completion:
            self.mutate(
                "POST",
                "/tasks/task-manager-web/runs/tmrun-manager-web/"
                "nodes/finalize/approve-completion",
                {
                    "expected_run_revision": 1,
                    "rationale": "planner cannot approve final completion",
                },
                token=token,
                base=self.task_manager_base,
            )
        self.assertEqual(planner_cannot_approve_completion.exception.code, 403)

        for command, payload in (
            (
                "approve-checkpoint",
                {
                    "expected_run_revision": 1,
                    "rationale": "planner role is not approval authority",
                },
            ),
            ("integrate-checkpoint", {"expected_run_revision": 1}),
        ):
            with self.subTest(command=command):
                with self.assertRaises(HTTPError) as planner_cannot_integrate:
                    self.mutate(
                        "POST",
                        "/tasks/task-manager-web/runs/tmrun-manager-web/"
                        f"nodes/scope/{command}",
                        payload,
                        token=token,
                        base=self.task_manager_base,
                    )
                self.assertEqual(planner_cannot_integrate.exception.code, 403)

        listing = self.get_json(
            "/tasks", token=token, base=self.task_manager_base
        )
        self.assertEqual(listing["tasks"][0]["task_id"], "task-manager-web")
        detail = self.get_json(
            "/tasks/task-manager-web",
            token=token,
            base=self.task_manager_base,
        )
        self.assertEqual(detail["task"]["record_hash"], finalized["task"]["record_hash"])
        self.assertEqual(detail["execution_run"]["run_id"], "tmrun-manager-web")

    def test_human_action_feedback_and_assistant_endpoints_are_revision_bound(self) -> None:
        execution = TaskManagerExecutionService(
            LocalTaskManagerRunStore(self.root / "task-manager-runs"),
            self.artifacts,
            actor_id=self.principal.subject_id,
            targets=FixtureExecutionTargetRegistry(),
            human_action_assistant=LocalHumanActionAssistant(),
        )
        self.server.task_manager_service.execution = execution
        self.server.principal = self.principal.model_copy(
            update={"roles": ("planner", "approver", "quick-agent")}
        )
        planning = self.server.orchestration_service
        created = planning.create(
            "验证人类门禁反馈与只读 Agent 辅助",
            plan_id="human-action-web",
        )
        assert created.snapshot.pending_proposal is not None
        applied = planning.apply_proposal(
            created.plan_id,
            created.snapshot.pending_proposal.proposal_id,
            expected_revision=created.sequence,
        )
        scope = next(node for node in applied.snapshot.nodes if node.node_id == "scope")
        gated = planning.update_node(
            applied.plan_id,
            scope.node_id,
            expected_revision=applied.sequence,
            title=scope.title,
            description=scope.description,
            kind=TaskPlanNodeKind.HUMAN_GATE,
            executor_hint="authorized scope owner",
            acceptance_criteria=scope.acceptance_criteria,
            deliverables=scope.deliverables,
            constraints=scope.constraints,
            risks=scope.risks,
            verification_requirements=scope.verification_requirements,
            requires_human_decision=True,
        )
        target = execution.resolve_target("fixture-project-target-v1")
        targeted = planning.set_execution_target(
            gated.plan_id,
            target.target_id,
            target.source_bundle_sha256,
            expected_revision=gated.sequence,
        )
        finalized = planning.finalize(
            targeted.plan_id,
            expected_revision=targeted.sequence,
        )
        assembled = execution.assemble(
            finalized,
            expected_plan_revision=finalized.sequence,
            run_id="tmrun-human-action-web",
        )

        session = self.get_json("/session", base=self.task_manager_base)
        self.assertTrue(session["capabilities"]["human_action_feedback"])
        self.assertTrue(session["capabilities"]["human_action_agent"])
        token = session["submission_token"]
        detail = self.get_json(
            "/tasks/human-action-web",
            token=token,
            base=self.task_manager_base,
        )
        guidance = detail["human_actions"][0]
        self.assertEqual(guidance["kind"], "scope_confirmation")

        assisted = self.mutate(
            "POST",
            "/tasks/human-action-web/runs/tmrun-human-action-web/"
            f"human-actions/{guidance['guidance_id']}/assistant",
            {
                "expected_plan_revision": guidance["expected_plan_revision"],
                "expected_run_revision": guidance["expected_run_revision"],
                "content": "请解释本轮范围确认需要核对什么。",
            },
            token=token,
            base=self.task_manager_base,
        )
        self.assertEqual(assisted["execution_run"]["revision"], assembled.sequence + 2)
        scope_node = next(
            node for node in assisted["execution_run"]["nodes"]
            if node["node"]["node_id"] == "scope"
        )
        self.assertEqual(scope_node["workflow_state"], "plan_review")
        self.assertEqual(
            [item["kind"] for item in scope_node["human_interactions"]],
            ["assistant_request", "assistant_response"],
        )

        current = assisted["human_actions"][0]
        revise = next(item for item in current["decisions"] if item["command"] == "discuss")
        feedback = self.mutate(
            "POST",
            "/tasks/human-action-web/runs/tmrun-human-action-web/"
            f"human-actions/{current['guidance_id']}/feedback",
            {
                "expected_plan_revision": current["expected_plan_revision"],
                "expected_run_revision": current["expected_run_revision"],
                "decision_id": revise["decision_id"],
                "content": "执行范围需要补充禁止修改路径后再确认。",
            },
            token=token,
            base=self.task_manager_base,
        )
        self.assertEqual(feedback["execution_run"]["revision"], assembled.sequence + 3)
        with self.assertRaises(HTTPError) as stale:
            self.mutate(
                "POST",
                "/tasks/human-action-web/runs/tmrun-human-action-web/"
                f"human-actions/{guidance['guidance_id']}/assistant",
                {
                    "expected_plan_revision": guidance["expected_plan_revision"],
                    "expected_run_revision": guidance["expected_run_revision"],
                    "content": "不得复用旧 guidance。",
                },
                token=token,
                base=self.task_manager_base,
            )
        self.assertEqual(stale.exception.code, 409)

    def test_task_manager_coordinator_tick_is_capability_and_revision_bound(self) -> None:
        executor = FixtureManagedExecutor(self.artifacts)
        execution = TaskManagerExecutionService(
            LocalTaskManagerRunStore(self.root / "task-manager-runs"),
            self.artifacts,
            actor_id=self.principal.subject_id,
            executor=executor,
            targets=FixtureExecutionTargetRegistry(),
        )
        leases = SqliteWorkerLeaseStore(self.root / "coordinator-leases.sqlite3")
        leases.migrate()
        coordinator = TaskManagerSerialCoordinator(
            execution,
            LocalTaskManagerCoordinatorStore(self.root / "coordinators"),
            leases,
            worker_id="fixture-web-coordinator",
        )
        self.server.task_manager_service.execution = execution
        self.server.task_manager_service.coordinator = coordinator
        self.server.principal = self.principal.model_copy(
            update={"roles": ("planner", "executor")}
        )

        session = self.get_json("/session", base=self.task_manager_base)
        self.assertTrue(session["capabilities"]["serial_coordinator"])
        self.assertTrue(session["capabilities"]["task_execution"])
        token = session["submission_token"]
        created = self.mutate(
            "POST",
            "/tasks",
            {"task_description": "推进一个真实 run", "task_id": "coordinator-web"},
            token=token,
            base=self.task_manager_base,
        )
        proposal = created["plan"]["pending_proposal"]
        self.assertEqual(created["human_actions"][0]["kind"], "proposal_decision")
        self.assertEqual(
            created["human_actions"][0]["expected_plan_revision"],
            created["task"]["revision"],
        )
        self.assertGreaterEqual(len(created["human_actions"][0]["steps"]), 5)
        applied = self.mutate(
            "POST",
            "/tasks/coordinator-web/proposal/apply",
            {
                "proposal_id": proposal["proposal_id"],
                "expected_revision": created["task"]["revision"],
            },
            token=token,
            base=self.task_manager_base,
        )
        targeted = self.mutate(
            "POST",
            "/tasks/coordinator-web/target",
            {
                "target_id": "fixture-project-target-v1",
                "expected_revision": applied["task"]["revision"],
            },
            token=token,
            base=self.task_manager_base,
        )
        finalized = self.mutate(
            "POST",
            "/tasks/coordinator-web/finalize",
            {"expected_revision": targeted["task"]["revision"]},
            token=token,
            base=self.task_manager_base,
        )
        assembled = self.mutate(
            "POST",
            "/tasks/coordinator-web/runs",
            {
                "expected_revision": finalized["task"]["revision"],
                "run_id": "tmrun-coordinator-web",
            },
            token=token,
            base=self.task_manager_base,
        )

        advanced = self.mutate(
            "POST",
            "/tasks/coordinator-web/runs/tmrun-coordinator-web/coordinator/tick",
            {"expected_run_revision": assembled["execution_run"]["revision"]},
            token=token,
            base=self.task_manager_base,
        )
        self.assertEqual(advanced["execution_run"]["revision"], 3)
        self.assertEqual(advanced["nodes"][0]["state"], "running")
        self.assertEqual(advanced["coordinator"]["cursor_node_id"], "scope")
        self.assertEqual(advanced["coordinator"]["next_action"], "observe_node")
        self.assertEqual(len(executor.start_requests), 1)

        with self.assertRaises(HTTPError) as stale:
            self.mutate(
                "POST",
                "/tasks/coordinator-web/runs/tmrun-coordinator-web/coordinator/tick",
                {"expected_run_revision": 1},
                token=token,
                base=self.task_manager_base,
            )
        self.assertEqual(stale.exception.code, 409)
        self.assertEqual(len(executor.start_requests), 1)

    def test_quick_agent_is_independent_authenticated_and_mode_authorized(self) -> None:
        fixture = FixtureQuickAgent()
        self.server.quick_agent = fixture
        token = self.server.session_token

        with self.assertRaises(HTTPError) as planner_denied:
            self.get_json(
                "/quick-agent/capacity",
                token=token,
                base=self.task_manager_base,
            )
        self.assertEqual(planner_denied.exception.code, 403)

        self.server.principal = self.principal.model_copy(
            update={"roles": ("planner", "quick-agent")}
        )
        session = self.get_json("/session", base=self.task_manager_base)
        self.assertTrue(session["capabilities"]["quick_agent_discussion"])
        self.assertTrue(session["capabilities"]["quick_agent_execution"])

        capacity = self.get_json(
            "/quick-agent/capacity?refresh=1",
            token=token,
            base=self.task_manager_base,
        )
        self.assertEqual(capacity["buckets"][0]["primary"]["remaining_percent"], 75)
        self.assertEqual(fixture.force_refreshes, [True])

        models = self.get_json(
            "/quick-agent/models?refresh=1",
            token=token,
            base=self.task_manager_base,
        )
        self.assertEqual(models["models"][0]["multi_agent_version"], "v2")
        self.assertEqual(fixture.model_force_refreshes, [True])

        discussion = self.mutate(
            "POST",
            "/quick-agent/messages",
            {
                "content": "inspect only",
                "mode": "discuss",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "low",
                "service_tier": None,
            },
            token=token,
            base=self.task_manager_base,
        )
        execution = self.mutate(
            "POST",
            "/quick-agent/messages",
            {
                "content": "make a small edit",
                "mode": "execute",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "service_tier": "priority",
            },
            token=token,
            base=self.task_manager_base,
        )
        self.assertEqual(discussion["events"][0]["mode"], "discuss")
        self.assertEqual(execution["events"][1]["mode"], "execute")
        self.assertEqual(
            fixture.started,
            [
                ("inspect only", QuickAgentMode.DISCUSS, "gpt-5.6-sol", "low", None),
                (
                    "make a small edit",
                    QuickAgentMode.EXECUTE,
                    "gpt-5.6-sol",
                    "xhigh",
                    "priority",
                ),
            ],
        )

        self.mutate(
            "POST", "/quick-agent/cancel", {}, token=token, base=self.task_manager_base
        )
        self.mutate(
            "POST",
            "/quick-agent/new-session",
            {},
            token=token,
            base=self.task_manager_base,
        )
        self.assertEqual(fixture.cancelled, 1)
        self.assertEqual(fixture.reset, 1)

    def test_authenticated_api_supports_discussion_crud_split_and_finalization(self) -> None:
        session = self.get_json("/session")
        self.assertEqual(session["principal"]["subject_id"], self.principal.subject_id)
        self.assertFalse(session["capabilities"]["workflow_execution"])
        self.assertEqual(session["agent_adapter"], "local-planning-fixture-v1")
        self.assertTrue(session["capabilities"]["planning_artifact_viewer"])
        token = session["submission_token"]

        with self.assertRaises(HTTPError) as unauthorized:
            self.mutate(
                "POST",
                "/plans",
                {"task_description": "unauthorized"},
                token=None,
            )
        self.assertEqual(unauthorized.exception.code, 403)

        created = self.mutate(
            "POST",
            "/plans",
            {"task_description": "实现可调整的任务编排", "plan_id": "plan-web"},
            token=token,
        )
        proposal = created["snapshot"]["pending_proposal"]
        applied = self.mutate(
            "POST",
            "/plans/plan-web/proposals/apply",
            {"proposal_id": proposal["proposal_id"], "expected_revision": 1},
            token=token,
        )
        created_node = self.mutate(
            "POST",
            "/plans/plan-web/nodes",
            {
                "title": "记录回滚策略",
                "description": "新增一个明确任务。",
                "source_node_id": "design",
                "relation": "subtask",
                "expected_revision": applied["sequence"],
            },
            token=token,
        )
        node = created_node["snapshot"]["nodes"][-1]
        updated = self.mutate(
            "PATCH",
            f"/plans/plan-web/nodes/{node['node_id']}",
            {
                "title": "记录版本与回滚策略",
                "description": "保留每次编排调整。",
                "kind": "validation",
                "executor_hint": None,
                "acceptance_criteria": ["版本与回滚信息可以复核"],
                "deliverables": [],
                "constraints": [],
                "risks": [],
                "verification_requirements": ["读取历史并核对哈希链"],
                "requires_human_decision": False,
                "expected_revision": created_node["sequence"],
            },
            token=token,
        )
        split = self.mutate(
            "POST",
            "/plans/plan-web/nodes/design/split",
            {
                "title": "并行 UI 评审",
                "description": "保持主线不阻塞。",
                "relation": "branch",
                "expected_revision": updated["sequence"],
            },
            token=token,
        )
        branch_id = split["snapshot"]["nodes"][-1]["node_id"]
        deleted = self.mutate(
            "DELETE",
            f"/plans/plan-web/nodes/{branch_id}",
            {"expected_revision": split["sequence"]},
            token=token,
        )
        finalized = self.mutate(
            "POST",
            "/plans/plan-web/finalize",
            {"expected_revision": deleted["sequence"]},
            token=token,
        )

        self.assertEqual(finalized["snapshot"]["status"], "finalized")
        self.assertEqual(
            finalized["snapshot"]["latest_finalized_revision"],
            finalized["sequence"],
        )
        history = self.get_json("/plans/plan-web/history")["history"]
        self.assertEqual(len(history), finalized["sequence"])
        self.assertEqual(history[-1]["record_hash"], finalized["record_hash"])
        assessment = self.get_json("/plans/plan-web/assessment")
        self.assertTrue(assessment["ready_to_finalize"])

        with self.assertRaises(HTTPError) as stale:
            self.mutate(
                "POST",
                "/plans/plan-web/messages",
                {"content": "增加支线", "expected_revision": 1},
                token=token,
            )
        self.assertEqual(stale.exception.code, 409)

    def test_api_supports_proposal_rejection_edges_derivation_and_archive(self) -> None:
        token = self.get_json("/session")["submission_token"]
        created = self.mutate(
            "POST",
            "/plans",
            {"task_description": "管理计划版本", "plan_id": "plan-manage"},
            token=token,
        )
        rejected = self.mutate(
            "POST",
            "/plans/plan-manage/proposals/reject",
            {
                "proposal_id": created["snapshot"]["pending_proposal"]["proposal_id"],
                "expected_revision": created["sequence"],
            },
            token=token,
        )
        first = self.mutate(
            "POST",
            "/plans/plan-manage/nodes",
            {
                "title": "起点",
                "acceptance_criteria": ["起点已确认"],
                "deliverables": ["输入说明"],
                "expected_revision": rejected["sequence"],
            },
            token=token,
        )
        second = self.mutate(
            "POST",
            "/plans/plan-manage/nodes",
            {
                "title": "终点",
                "kind": "human_gate",
                "acceptance_criteria": ["用户已确认"],
                "requires_human_decision": True,
                "expected_revision": first["sequence"],
            },
            token=token,
        )
        edge = self.mutate(
            "POST",
            "/plans/plan-manage/edges",
            {
                "source_node_id": first["snapshot"]["nodes"][0]["node_id"],
                "target_node_id": second["snapshot"]["nodes"][1]["node_id"],
                "relation": "sequence",
                "label": "主线",
                "expected_revision": second["sequence"],
            },
            token=token,
        )
        edge_id = edge["snapshot"]["edges"][0]["edge_id"]
        changed = self.mutate(
            "PATCH",
            f"/plans/plan-manage/edges/{edge_id}",
            {
                "relation": "dependency",
                "label": "前置",
                "expected_revision": edge["sequence"],
            },
            token=token,
        )
        derived = self.mutate(
            "POST",
            f"/plans/plan-manage/revisions/{changed['sequence']}/derive",
            {"expected_revision": changed["sequence"]},
            token=token,
        )
        archived = self.mutate(
            "POST",
            "/plans/plan-manage/archive",
            {"archived": True, "expected_revision": derived["sequence"]},
            token=token,
        )
        self.assertTrue(archived["snapshot"]["is_archived"])
        restored = self.mutate(
            "POST",
            "/plans/plan-manage/archive",
            {"archived": False, "expected_revision": archived["sequence"]},
            token=token,
        )
        deleted = self.mutate(
            "DELETE",
            f"/plans/plan-manage/edges/{edge_id}",
            {"expected_revision": restored["sequence"]},
            token=token,
        )
        self.assertEqual(deleted["snapshot"]["edges"], [])
        self.assertFalse(deleted["snapshot"]["is_archived"])

    def test_planning_artifact_viewer_is_authenticated_bound_and_bounded(self) -> None:
        token = self.get_json("/session")["submission_token"]
        self.mutate(
            "POST",
            "/plans",
            {"task_description": "检查规划证据", "plan_id": "plan-artifacts"},
            token=token,
        )
        self.mutate(
            "POST",
            "/plans",
            {"task_description": "另一个计划", "plan_id": "plan-other"},
            token=token,
        )
        prompt = self.artifacts.register_bytes(
            task_id="plan-artifacts",
            run_id="planning-invocation-1",
            artifact_type="task_plan.agent.prompt",
            producer="codex-cli-planning-v1",
            content=("规划证据\n" + "x" * 1_500).encode("utf-8"),
            metadata={"based_on_revision": 1},
        )
        self.artifacts.register_bytes(
            task_id="plan-artifacts",
            run_id="planning-invocation-1",
            artifact_type="unrelated.private.artifact",
            producer="fixture",
            content=b"must not be listed",
        )
        self.artifacts.register_bytes(
            task_id="another-plan",
            run_id="planning-invocation-2",
            artifact_type="task_plan.agent.stderr",
            producer="fixture",
            content=b"must remain plan-bound",
        )

        with self.assertRaises(HTTPError) as unauthorized:
            self.get_json("/plans/plan-artifacts/artifacts")
        self.assertEqual(unauthorized.exception.code, 403)

        listing = self.get_json(
            "/plans/plan-artifacts/artifacts", token=token
        )
        self.assertEqual(len(listing["artifacts"]), 1)
        descriptor = listing["artifacts"][0]
        self.assertEqual(descriptor["artifact_id"], prompt.artifact_id)
        self.assertNotIn("uri", descriptor)
        self.assertEqual(descriptor["plan_id"], "plan-artifacts")

        preview = self.get_json(
            f"/plans/plan-artifacts/artifacts/{prompt.artifact_id}",
            token=token,
        )
        self.assertTrue(preview["content_verified"])
        self.assertTrue(preview["truncated"])
        self.assertEqual(preview["preview_size_bytes"], 1_024)
        self.assertNotIn("uri", preview["artifact"])

        with self.assertRaises(HTTPError) as wrong_plan:
            self.get_json(
                f"/plans/plan-other/artifacts/{prompt.artifact_id}",
                token=token,
            )
        self.assertEqual(wrong_plan.exception.code, 404)

    def test_rejects_non_loopback_binding(self) -> None:
        with self.assertRaises(TaskOrchestrationServerError):
            create_task_orchestration_server(
                self.server.orchestration_service,
                self.principal,
                host="0.0.0.0",
            )

    def test_expired_planner_session_rejects_mutation(self) -> None:
        now = datetime.now(UTC)
        self.server.principal = self.principal.model_copy(
            update={
                "authenticated_at": now - timedelta(minutes=10),
                "valid_until": now - timedelta(minutes=5),
            }
        )

        with self.assertRaises(HTTPError) as expired:
            self.mutate(
                "POST",
                "/plans",
                {"task_description": "must be rejected"},
                token=self.server.session_token,
            )

        self.assertEqual(expired.exception.code, 403)
        self.assertEqual(
            self.server.orchestration_service.list_latest(), ()
        )

    def test_planning_provider_failure_returns_bad_gateway_without_a_revision(self) -> None:
        class FailingPlanningAgent:
            adapter_id = "failing-planning-fixture"

            def propose(self, **_kwargs):
                raise PlanningAgentError("planning provider unavailable")

        self.server.orchestration_service.planning_agent = FailingPlanningAgent()
        with self.assertRaises(HTTPError) as failed:
            self.mutate(
                "POST",
                "/plans",
                {"task_description": "must remain unrecorded", "plan_id": "plan-failed"},
                token=self.server.session_token,
            )

        self.assertEqual(failed.exception.code, 502)
        self.assertEqual(self.server.orchestration_service.list_latest(), ())


if __name__ == "__main__":
    unittest.main()
