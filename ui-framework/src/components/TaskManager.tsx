import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import {
  AlertTriangle,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  CirclePlus,
  Clock3,
  GitBranch,
  LoaderCircle,
  MessageSquareText,
  Network,
  RefreshCw,
  Send,
  ShieldCheck,
  UserRound,
  X,
} from "lucide-react";
import type {
  ActiveSemanticUIDesign,
  ManagedNodeState,
  ManagedNodeView,
  ManagedTaskDetail,
  ManagedTaskStage,
  ManagedTaskSummary,
  TaskManagerSession,
  TaskManagerHumanActionGuidance,
  TaskManagerHumanDecisionOption,
  TaskPlanEdge,
} from "../types";
import {
  readTaskManagerView,
  TaskManagerRail,
  type TaskManagerViewId,
} from "./task-manager/TaskManagerRail";
import {
  TaskManagerAgentStatus,
  TaskManagerControl,
  TaskManagerExecution,
  TaskManagerHome,
} from "./task-manager/TaskManagerViews";
import { HumanActionGuidanceCard } from "./task-manager/HumanActionGuidanceCard";
import "./task-manager/taskManagerShell.css";

const API_ROOT = "/api/task-manager";

type TaskNodeData = Record<string, unknown> & {
  title: string;
  kind: string;
  state: ManagedNodeState;
  dependencyCount: number;
  issueCount: number;
  humanAction: string | null;
};

type TaskGraphNode = Node<TaskNodeData, "managed-task">;

const stageLabels: Record<ManagedTaskStage, string> = {
  proposal_pending: "候选图待确认",
  planning: "规划中",
  ready: "已固化 · 待执行",
  running: "执行中",
  needs_attention: "需要处理",
  verifying: "验证中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  archived: "已归档",
};

const nodeStateLabels: Record<ManagedNodeState, string> = {
  proposed: "PROPOSED",
  planned: "PLANNED",
  ready: "READY",
  running: "RUNNING",
  waiting: "WAITING",
  blocked: "BLOCKED",
  verifying: "VERIFYING",
  completed: "COMPLETED",
  failed: "FAILED",
  cancelled: "CANCELLED",
};

const relationColors: Record<string, string> = {
  sequence: "#78818d",
  dependency: "#8995ff",
  branch: "#f2c86b",
  subtask: "#b9ff66",
};

async function api<T>(
  path: string,
  options: { method?: string; token?: string; body?: unknown } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.token) headers["X-JobSlayer-Session"] = options.token;
  const response = await fetch(`${API_ROOT}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const payload = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`);
  return payload as T;
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function ManagedTaskNode({ data, selected }: NodeProps<TaskGraphNode>) {
  return (
    <div className={`task-manager-node state-${data.state} ${selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="task-manager-node-meta">
        <span>{data.kind.replace("_", " ")}</span>
        <i>{nodeStateLabels[data.state]}</i>
      </div>
      <strong>{data.title}</strong>
      <small>
        {data.dependencyCount} dependencies
        {data.issueCount > 0 ? ` · ${data.issueCount} issues` : ""}
      </small>
      {data.humanAction && <em className="task-node-human-action"><UserRound size={11} /> 需要人工处理</em>}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { "managed-task": ManagedTaskNode };

function layoutGraph(
  nodes: ManagedNodeView[],
  edges: TaskPlanEdge[],
  humanActions: TaskManagerHumanActionGuidance[],
): { nodes: TaskGraphNode[]; edges: Edge[] } {
  const guidanceByNode = new Map(
    humanActions
      .filter((item) => item.node_id !== null)
      .map((item) => [item.node_id as string, item]),
  );
  const depth = new Map(nodes.map((item) => [item.node.node_id, 0]));
  for (let pass = 0; pass < nodes.length; pass += 1) {
    for (const edge of edges) {
      const sourceDepth = depth.get(edge.source_node_id) ?? 0;
      const targetDepth = depth.get(edge.target_node_id) ?? 0;
      if (targetDepth <= sourceDepth) depth.set(edge.target_node_id, sourceDepth + 1);
    }
  }
  const rows = new Map<number, number>();
  return {
    nodes: nodes.map((item) => {
      const column = depth.get(item.node.node_id) ?? 0;
      const row = rows.get(column) ?? 0;
      rows.set(column, row + 1);
      return {
        id: item.node.node_id,
        type: "managed-task",
        position: { x: 52 + column * 244, y: 52 + row * 136 },
        data: {
          title: item.node.title,
          kind: item.node.kind,
          state: item.state,
          dependencyCount: item.dependency_node_ids.length,
          issueCount: item.issue_codes.length,
          humanAction: guidanceByNode.get(item.node.node_id)?.title ?? null,
        },
      };
    }),
    edges: edges.map((edge) => ({
      id: edge.edge_id,
      source: edge.source_node_id,
      target: edge.target_node_id,
      label: edge.label ?? edge.relation,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: relationColors[edge.relation],
      },
      style: {
        stroke: relationColors[edge.relation],
        strokeDasharray: nodes.some((item) => item.state === "proposed") ? "5 5" : undefined,
      },
      labelStyle: { fill: relationColors[edge.relation], fontSize: 8 },
      labelBgStyle: { fill: "#0d1014", fillOpacity: 0.94 },
    })),
  };
}

interface TaskManagerProps {
  onNotice: (message: string) => void;
}

export function TaskManager({ onNotice }: TaskManagerProps) {
  const [activeView, setActiveView] = useState<TaskManagerViewId>(() => (
    readTaskManagerView(window.location.hash)
  ));
  const [session, setSession] = useState<TaskManagerSession | null>(null);
  const [uiDesign, setUiDesign] = useState<ActiveSemanticUIDesign | null>(null);
  const [tasks, setTasks] = useState<ManagedTaskSummary[]>([]);
  const [detail, setDetail] = useState<ManagedTaskDetail | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [taskDescription, setTaskDescription] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const navigate = (view: TaskManagerViewId) => {
    setActiveView(view);
    const nextHash = `#/${view}`;
    if (window.location.hash !== nextHash) {
      window.history.pushState(null, "", nextHash);
    }
  };

  useEffect(() => {
    const syncView = () => setActiveView(readTaskManagerView(window.location.hash));
    window.addEventListener("hashchange", syncView);
    window.addEventListener("popstate", syncView);
    const initialView = readTaskManagerView(window.location.hash);
    if (
      !window.location.hash
      || (initialView === "home" && window.location.hash !== "#/home")
    ) {
      window.history.replaceState(null, "", "#/home");
    }
    return () => {
      window.removeEventListener("hashchange", syncView);
      window.removeEventListener("popstate", syncView);
    };
  }, []);

  const refreshTasks = async (activeSession: TaskManagerSession) => {
    const listing = await api<{ tasks: ManagedTaskSummary[] }>("/tasks", {
      token: activeSession.submission_token,
    });
    setTasks(listing.tasks);
    return listing.tasks;
  };

  const loadTask = async (taskId: string, activeSession: TaskManagerSession) => {
    const next = await api<ManagedTaskDetail>(`/tasks/${encodeURIComponent(taskId)}`, {
      token: activeSession.submission_token,
    });
    setDetail(next);
    setSelectedNodeId((current) => (
      next.nodes.some((item) => item.node.node_id === current)
        ? current
        : next.nodes[0]?.node.node_id ?? null
    ));
    return next;
  };

  useEffect(() => {
    let cancelled = false;
    const start = async () => {
      try {
        const activeSession = await api<TaskManagerSession>("/session");
        if (cancelled) return;
        setSession(activeSession);
        if (activeSession.capabilities.semantic_ui_design) {
          const activeDesign = await api<ActiveSemanticUIDesign>("/ui-design", {
            token: activeSession.submission_token,
          });
          if (cancelled) return;
          setUiDesign(activeDesign);
        }
        const listing = await refreshTasks(activeSession);
        if (!cancelled && listing.length > 0) await loadTask(listing[0].task_id, activeSession);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      }
    };
    void start();
    return () => { cancelled = true; };
  }, []);

  const runEdges: TaskPlanEdge[] = detail?.execution_run?.nodes.flatMap((node) => (
    node.dependency_node_ids.map((dependencyNodeId) => ({
      schema_version: "1.0" as const,
      edge_id: `run-${dependencyNodeId}-${node.node.node_id}`,
      source_node_id: dependencyNodeId,
      target_node_id: node.node.node_id,
      relation: "dependency" as const,
      label: null,
    }))
  )) ?? [];
  const effectiveEdges = detail?.execution_run
    ? runEdges
    : detail?.plan.pending_proposal?.edges ?? detail?.plan.edges ?? [];
  const graph = useMemo(
    () => layoutGraph(detail?.nodes ?? [], effectiveEdges, detail?.human_actions ?? []),
    [detail, effectiveEdges],
  );
  const selectedNode = detail?.nodes.find((item) => item.node.node_id === selectedNodeId) ?? null;
  const selectedRunNode = detail?.execution_run?.nodes.find(
    (item) => item.node.node_id === selectedNodeId,
  ) ?? null;
  const selectedGuidance = detail?.human_actions.find(
    (item) => item.node_id === selectedNodeId,
  ) ?? null;
  const planGuidance = detail?.human_actions.find((item) => item.node_id === null) ?? null;

  const commit = async (
    path: string,
    body: unknown,
    successMessage: string,
  ): Promise<ManagedTaskDetail | null> => {
    if (!session) return null;
    setBusy(true);
    setError(null);
    try {
      const next = await api<ManagedTaskDetail>(path, {
        method: "POST",
        token: session.submission_token,
        body,
      });
      setDetail(next);
      await refreshTasks(session);
      onNotice(successMessage);
      return next;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      if (detail) {
        try {
          await Promise.all([
            loadTask(detail.task.task_id, session),
            refreshTasks(session),
          ]);
        } catch {
          // Keep the original command failure visible; explicit refresh remains available.
        }
      }
      return null;
    } finally {
      setBusy(false);
    }
  };

  const createTask = async (event: FormEvent) => {
    event.preventDefault();
    if (!session || !taskDescription.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api<ManagedTaskDetail>("/tasks", {
        method: "POST",
        token: session.submission_token,
        body: { task_description: taskDescription.trim() },
      });
      setDetail(next);
      setSelectedNodeId(next.nodes[0]?.node.node_id ?? null);
      setTaskDescription("");
      setShowCreate(false);
      await refreshTasks(session);
      onNotice("任务已创建；Agent 候选 DAG 等待你的决定。 ");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const sendMessage = async (event: FormEvent) => {
    event.preventDefault();
    if (!detail || detail.execution_run || !message.trim()) return;
    const content = message.trim();
    setMessage("");
    await commit(
      `/tasks/${encodeURIComponent(detail.task.task_id)}/messages`,
      {
        content,
        expected_revision: detail.task.revision,
        selected_node_id: selectedNodeId,
      },
      "讨论已记录；新的候选 DAG 等待确认。",
    );
  };

  const proposal = detail?.execution_run ? null : detail?.plan.pending_proposal ?? null;
  const terminalRun = detail?.execution_run?.stage === "completed"
    || detail?.execution_run?.stage === "cancelled";
  const targetBindingNeedsRefresh = detail?.execution_target_assessment?.issues.some(
    (issue) => issue.code === "target.source_binding_missing"
      || issue.code === "target.source_binding_drift",
  ) ?? false;
  const needsTargetBinding = detail?.plan.execution_target_id === null
    || targetBindingNeedsRefresh;
  const targetToBind = detail?.execution_targets.find(
    (target) => target.target_id === detail.plan.execution_target_id,
  )?.target_id ?? detail?.execution_targets[0]?.target_id ?? "";

  const bindExecutionTarget = (targetId: string) => {
    if (!detail || !targetId) return;
    void commit(
      `/tasks/${encodeURIComponent(detail.task.task_id)}/target`,
      { target_id: targetId, expected_revision: detail.task.revision },
      "执行目标已绑定；请核对预检结果后固化任务流。",
    );
  };

  const finalizePlan = () => {
    if (!detail) return;
    void commit(
      `/tasks/${encodeURIComponent(detail.task.task_id)}/finalize`,
      { expected_revision: detail.task.revision },
      "任务流已固化；下一步装配执行 Run。",
    );
  };

  const assembleRun = () => {
    if (!detail) return;
    void commit(
      `/tasks/${encodeURIComponent(detail.task.task_id)}/runs`,
      { expected_revision: detail.task.revision },
      "执行 Run 已装配；请在执行页推进。",
    ).then((next) => {
      if (next?.execution_run) navigate("execution");
    });
  };

  const refreshCurrent = () => {
    if (!session) return;
    void refreshTasks(session).then((listing) => {
      if (detail) return loadTask(detail.task.task_id, session);
      if (listing[0]) return loadTask(listing[0].task_id, session);
      return undefined;
    }).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
  };

  const advanceRun = () => {
    if (!detail?.execution_run) return;
    void commit(
      `/tasks/${encodeURIComponent(detail.task.task_id)}/runs/${encodeURIComponent(detail.execution_run.run_id)}/coordinator/tick`,
      { expected_run_revision: detail.execution_run.revision },
      "串行协调器已推进一个受治理动作。",
    );
  };

  const formalHumanActionCommands: Record<string, string> = {
    confirm_scope: "confirm-scope",
    accept_review: "accept-review",
    review_source: "review-source",
    approve_checkpoint: "approve-checkpoint",
    approve_completion: "approve-completion",
  };

  const isFormalHumanDecision = (decision: TaskManagerHumanDecisionOption) => (
    decision.command !== null && decision.command in formalHumanActionCommands
  );

  const canSubmitHumanDecision = (
    guidance: TaskManagerHumanActionGuidance,
    decision: TaskManagerHumanDecisionOption,
  ) => {
    if (!session || !isFormalHumanDecision(decision)) return false;
    if (guidance.kind === "scope_confirmation") return session.capabilities.task_planning;
    if (guidance.kind === "verified_deliverable_review") return session.capabilities.node_review;
    if (guidance.kind === "source_review") return session.capabilities.source_review;
    if (guidance.kind === "source_checkpoint_approval") {
      return session.capabilities.source_checkpoint_approval;
    }
    if (guidance.kind === "completion_approval") return session.capabilities.completion_approval;
    return false;
  };

  const submitHumanDecision = (
    guidance: TaskManagerHumanActionGuidance,
    decision: TaskManagerHumanDecisionOption,
    rationale: string,
  ) => {
    if (
      !detail?.execution_run
      || guidance.node_id === null
      || guidance.expected_run_revision === null
      || decision.command === null
    ) return;
    const command = formalHumanActionCommands[decision.command];
    if (!command) return;
    const body: Record<string, unknown> = {
      expected_run_revision: guidance.expected_run_revision,
      rationale,
    };
    if (command === "review-source") body.findings = [];
    void commit(
      `/tasks/${encodeURIComponent(detail.task.task_id)}/runs/${encodeURIComponent(detail.execution_run.run_id)}/nodes/${encodeURIComponent(guidance.node_id)}/${command}`,
      body,
      `${decision.label}已提交；请核对新的 Run revision 与审计记录。`,
    );
  };

  const recordHumanFeedback = (
    guidance: TaskManagerHumanActionGuidance,
    decision: TaskManagerHumanDecisionOption,
    content: string,
  ) => {
    if (!detail?.execution_run || guidance.expected_run_revision === null) return;
    void commit(
      `/tasks/${encodeURIComponent(detail.task.task_id)}/runs/${encodeURIComponent(detail.execution_run.run_id)}/human-actions/${encodeURIComponent(guidance.guidance_id)}/feedback`,
      {
        expected_plan_revision: guidance.expected_plan_revision,
        expected_run_revision: guidance.expected_run_revision,
        decision_id: decision.decision_id,
        content,
      },
      "验收反馈已写入追加式 Run 记录；节点状态保持等待。",
    );
  };

  const askHumanActionAgent = (
    guidance: TaskManagerHumanActionGuidance,
    content: string,
  ) => {
    if (!detail?.execution_run || guidance.expected_run_revision === null) return;
    void commit(
      `/tasks/${encodeURIComponent(detail.task.task_id)}/runs/${encodeURIComponent(detail.execution_run.run_id)}/human-actions/${encodeURIComponent(guidance.guidance_id)}/assistant`,
      {
        expected_plan_revision: guidance.expected_plan_revision,
        expected_run_revision: guidance.expected_run_revision,
        content,
      },
      "任务绑定 Agent 已回复；回复只用于解释或起草反馈。",
    );
  };

  const selectTask = (taskId: string) => {
    if (session && taskId) {
      void loadTask(taskId, session).catch((cause) => (
        setError(cause instanceof Error ? cause.message : String(cause))
      ));
    }
  };

  const openCreate = () => {
    navigate("orchestration");
    setShowCreate(true);
  };

  const sharedViewProps = {
    session,
    uiDesign,
    tasks,
    detail,
    busy,
    error,
    onNavigate: navigate,
    onSelectTask: selectTask,
    onRefresh: refreshCurrent,
    onNewTask: openCreate,
    onAdvanceRun: advanceRun,
    onSubmitHumanDecision: submitHumanDecision,
    onRecordHumanFeedback: recordHumanFeedback,
    onAskHumanActionAgent: askHumanActionAgent,
    canSubmitHumanDecision,
    isFormalHumanDecision,
  };

  return (
    <div className="task-manager-workspace">
      <TaskManagerRail
        activeView={activeView}
        connected={session !== null}
        attentionCount={tasks.filter((task) => (
          task.blocker_count > 0 || task.stage === "needs_attention"
        )).length}
        onSelect={navigate}
      />
      <main className="task-manager-view-host">
        {activeView === "home" && <TaskManagerHome {...sharedViewProps} />}
        {activeView === "agent" && <TaskManagerAgentStatus {...sharedViewProps} />}
        {activeView === "control" && <TaskManagerControl {...sharedViewProps} />}
        {activeView === "execution" && <TaskManagerExecution {...sharedViewProps} />}
        {activeView === "orchestration" && (
        <div className="task-manager-page page-enter">
      <header className="task-manager-header">
        <div className="task-manager-heading">
          <h1>Task Graph</h1>
          <p>预览节点、检查细节、与 Agent 调整任务。</p>
        </div>
        {uiDesign && (
          <div
            className="task-manager-design-state"
            title={uiDesign.description.design_intent}
          >
            <span><ShieldCheck size={12} /> {uiDesign.binding.scheme_id} · V{uiDesign.binding.revision}</span>
            <small>
              {uiDesign.state_counts.dirty} DIRTY · {uiDesign.state_counts.planned} PLANNED · {uiDesign.state_counts.stable} STABLE
            </small>
          </div>
        )}
        <div className="task-manager-task-picker">
          <label htmlFor="task-manager-picker">当前任务</label>
          <div>
            <select
              id="task-manager-picker"
              value={detail?.task.task_id ?? ""}
              disabled={!session || busy || tasks.length === 0}
              onChange={(event) => {
                if (session && event.target.value) void loadTask(event.target.value, session);
              }}
            >
              {tasks.length === 0 && <option value="">暂无任务</option>}
              {tasks.map((task) => (
                <option key={task.task_id} value={task.task_id}>
                  {stageLabels[task.stage]} · {task.title}
                </option>
              ))}
            </select>
            <ChevronDown size={14} />
          </div>
        </div>
        <button className="button button-primary" onClick={() => setShowCreate(true)}>
          <CirclePlus size={15} /> 新任务
        </button>
        <button
          className="button button-quiet"
          disabled={!session || busy}
          onClick={refreshCurrent}
        >
          <RefreshCw size={14} /> 刷新
        </button>
      </header>

      {error && (
        <div className="task-manager-alert" role="alert">
          <AlertTriangle size={15} /><span>{error}</span><button onClick={() => setError(null)}><X size={14} /></button>
        </div>
      )}

      {!session && !error && (
        <div className="task-manager-loading"><LoaderCircle size={20} /> 正在连接本地 TaskManager API…</div>
      )}

      {session && !detail && !showCreate && (
        <section className="task-manager-empty">
          <Network size={38} />
          <h2>从一段任务描述开始</h2>
          <p>Agent 会形成候选 DAG；只有你应用并固化的版本才成为后续执行输入。</p>
          <button className="button button-primary" onClick={() => setShowCreate(true)}><CirclePlus size={15} /> 创建第一个任务</button>
        </section>
      )}

      {showCreate && (
        <section className="task-manager-create">
          <div>
            <span>NEW TASK</span>
            <h2>描述你想完成的工作</h2>
            <p>可以先保持目标级描述，再通过右侧 Agent 对话逐轮具体化。</p>
          </div>
          <form onSubmit={createTask}>
            <textarea autoFocus value={taskDescription} onChange={(event) => setTaskDescription(event.target.value)} placeholder="例如：基于 Anygine 开发一个小型交互 App，并提供可重复的构建与运行验证。" />
            <div><button type="button" className="button button-quiet" onClick={() => setShowCreate(false)}>取消</button><button className="button button-primary" disabled={busy || !taskDescription.trim()}>{busy ? <LoaderCircle size={14} /> : <Bot size={14} />} 生成候选 DAG</button></div>
          </form>
        </section>
      )}

      {detail && !showCreate && (
        <>
          <div className="task-manager-statusbar">
            <span className={`task-stage stage-${detail.task.stage}`}><i /> {stageLabels[detail.task.stage]}</span>
            <span><strong>REV {detail.task.revision}</strong> · {detail.task.node_count} nodes</span>
            {detail.execution_run && <span><strong>RUN {detail.execution_run.run_id}</strong> · R{detail.execution_run.revision} · {detail.execution_run.stage.toUpperCase()}</span>}
            <span className="task-manager-truth"><ShieldCheck size={13} /> {proposal ? "候选图预览" : "已应用任务图"}</span>
          </div>

          {proposal && (
            <div className="task-manager-proposal">
              <Bot size={17} />
              <div><strong>Agent 提交了候选 DAG</strong><span>{proposal.summary}</span></div>
              <button className="button button-quiet" disabled={busy} onClick={() => void commit(`/tasks/${encodeURIComponent(detail.task.task_id)}/proposal/reject`, { proposal_id: proposal.proposal_id, expected_revision: detail.task.revision }, "候选 DAG 已拒绝；已应用任务图保持不变。")}>拒绝</button>
              <button className="button button-primary" disabled={busy} onClick={() => void commit(`/tasks/${encodeURIComponent(detail.task.task_id)}/proposal/apply`, { proposal_id: proposal.proposal_id, expected_revision: detail.task.revision }, "候选 DAG 已应用为新的规划 revision。")}>{busy ? <LoaderCircle size={14} /> : <Check size={14} />} 应用候选图</button>
            </div>
          )}

          {detail.execution_run ? (
            <section className={`task-closure-next run-${detail.execution_run.stage}`}>
              <div>
                <span>{detail.execution_run.stage === "completed" ? "CLOSED LOOP" : terminalRun ? "TERMINAL RUN" : "ACTIVE RUN"}</span>
                <strong>{detail.execution_run.stage === "completed" ? "任务闭环已完成" : detail.execution_run.stage === "cancelled" ? "执行 Run 已取消" : "执行 Run 已装配，规划输入已锁定"}</strong>
                <small>
                  Run {detail.execution_run.run_id} · 绑定 Plan R{detail.execution_run.plan_revision} · Run R{detail.execution_run.revision}
                  {detail.task.revision !== detail.execution_run.plan_revision
                    ? `；后续规划记录 R${detail.task.revision} 不会覆盖该 Run`
                    : ""}
                </small>
              </div>
              <button className="button button-primary" type="button" onClick={() => navigate("execution")}>
                <ShieldCheck size={14} /> {terminalRun ? "查看执行记录" : "进入执行页"}
              </button>
            </section>
          ) : !proposal && (
            <section className="task-closure-next">
              <div>
                <span>NEXT REQUIRED ACTION</span>
                {needsTargetBinding ? (
                  <>
                    <strong>1. {targetBindingNeedsRefresh ? "更新执行目标绑定" : "绑定执行目标"}</strong>
                    <small>{targetBindingNeedsRefresh ? "目标基线或验证契约已经更新；重新绑定会形成新 Plan revision，但不会启动 Agent。" : "选择目标只固定仓库、基线和执行约束，不会启动 Agent。"}</small>
                  </>
                ) : detail.plan.status === "draft" ? (
                  <>
                    <strong>2. 固化当前任务流</strong>
                    <small>固化前必须消除目标预检阻塞；固化本身不会启动执行。</small>
                  </>
                ) : (
                  <>
                    <strong>3. 装配执行 Run</strong>
                    <small>Run 会绑定当前 Plan revision/hash；装配完成后再到执行页逐步推进。</small>
                  </>
                )}
                {detail.execution_blockers.length > 0 && !needsTargetBinding && (
                  <ul>{detail.execution_blockers.map((item) => <li key={item}>{item}</li>)}</ul>
                )}
              </div>
              {needsTargetBinding ? (
                <button
                  className="button button-primary"
                  type="button"
                  disabled={busy || !targetToBind}
                  onClick={() => bindExecutionTarget(targetToBind)}
                >
                  <Network size={14} /> {targetBindingNeedsRefresh ? "更新目标绑定" : "绑定默认目标"}
                </button>
              ) : detail.plan.status === "draft" ? (
                <button
                  className="button button-primary"
                  type="button"
                  disabled={busy || !detail.assessment.ready_to_finalize || !detail.execution_target_assessment?.ready}
                  onClick={finalizePlan}
                >
                  <Check size={14} /> 固化任务流
                </button>
              ) : (
                <button
                  className="button button-primary"
                  type="button"
                  disabled={busy || !detail.run_assembly_available}
                  onClick={assembleRun}
                >
                  <CirclePlus size={14} /> 装配执行 Run
                </button>
              )}
            </section>
          )}

          <div className="task-manager-layout">
            <section className="task-manager-main panel-surface">
              <header className="task-manager-panel-title">
                <span><Network size={14} /> TASK GRAPH</span>
                <small>{proposal
                  ? "PREVIEW · NOT APPLIED"
                  : detail.execution_run
                    ? `RUN PLAN R${detail.execution_run.plan_revision}`
                    : detail.plan.status.toUpperCase()}</small>
              </header>
              <div className="task-manager-graph">
                <ReactFlow
                  key={`${detail.task.task_id}-${detail.task.revision}-${detail.execution_run?.revision ?? 0}`}
                  nodes={graph.nodes}
                  edges={graph.edges}
                  nodeTypes={nodeTypes}
                  fitView
                  fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
                  minZoom={0.25}
                  maxZoom={1.4}
                  nodesDraggable={false}
                  nodesConnectable={false}
                  elementsSelectable
                  onNodeClick={(_, node) => setSelectedNodeId(node.id)}
                >
                  <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#303640" />
                  <Controls showInteractive={false} />
                </ReactFlow>
              </div>
            </section>

            <aside className="task-manager-side panel-surface">
              <section className="task-manager-node-inspector">
                <header className="task-manager-panel-title">
                  <span><GitBranch size={14} /> NODE DETAIL</span>
                  <small>{selectedNode ? nodeStateLabels[selectedNode.state] : "NO SELECTION"}</small>
                </header>
                <div className="task-manager-detail">
                  {planGuidance && <HumanActionGuidanceCard guidance={planGuidance} compact />}
                  {selectedNode ? (
                    <>
                      <div className="detail-eyebrow">{selectedNode.node.kind}</div>
                      <h2>{selectedNode.node.title}</h2>
                      <code>{selectedNode.node.node_id}</code>
                      <p>{selectedNode.node.description || "暂无节点描述。"}</p>
                      <dl>
                        <div><dt>状态</dt><dd><i className={`node-state state-${selectedNode.state}`}>{nodeStateLabels[selectedNode.state]}</i></dd></div>
                        <div><dt>执行建议</dt><dd>{selectedNode.node.executor_hint ?? "未指定"}</dd></div>
                        <div><dt>前置节点</dt><dd>{selectedNode.dependency_node_ids.join(", ") || "无"}</dd></div>
                        {selectedRunNode && <div><dt>内核状态</dt><dd>{selectedRunNode.workflow_state}</dd></div>}
                      </dl>
                      <section><span>ACCEPTANCE CRITERIA</span>{selectedNode.node.acceptance_criteria.length ? selectedNode.node.acceptance_criteria.map((item) => <p key={item}><CheckCircle2 size={13} /> {item}</p>) : <p>尚未定义</p>}</section>
                      <section><span>VERIFICATION</span>{selectedNode.node.verification_requirements.length ? selectedNode.node.verification_requirements.map((item) => <p key={item}><ShieldCheck size={13} /> {item}</p>) : <p>尚未定义</p>}</section>
                      {selectedRunNode?.latest_observation && <section><span>LATEST FEEDBACK</span><p><Clock3 size={13} /> {selectedRunNode.latest_observation.summary}</p></section>}
                      {selectedGuidance && <HumanActionGuidanceCard guidance={selectedGuidance} compact />}
                    </>
                  ) : (
                    <div className="task-manager-detail-empty"><GitBranch size={24} /><p>在左侧任务图中选择一个节点。</p></div>
                  )}
                </div>
              </section>

              <section className="task-manager-agent-panel">
                <header className="task-manager-panel-title">
                  <span><MessageSquareText size={14} /> AGENT DIALOG</span>
                  <small>{detail.plan.conversation.length} MESSAGES</small>
                </header>
                <div className="task-manager-agent">
                  <div className="task-manager-conversation">
                    {detail.plan.conversation.map((entry) => (
                      <article key={entry.message_id} className={entry.role}>
                        <span>{entry.role === "user" ? <UserRound size={14} /> : <Bot size={14} />}</span>
                        <div><strong>{entry.role === "user" ? session?.principal.display_name ?? "User" : entry.agent_adapter ?? "TaskManager"}</strong><p>{entry.content}</p><time>{formatTime(entry.created_at)}</time></div>
                      </article>
                    ))}
                  </div>
                  {detail.execution_run ? (
                    <div className="agent-plan-locked">
                      <ShieldCheck size={18} />
                      <div><strong>规划输入已锁定</strong><span>执行反馈请到执行页处理；新目标或修改应创建新任务，不能覆盖既有 Run。</span></div>
                      <button className="button button-quiet" type="button" onClick={() => setShowCreate(true)}><CirclePlus size={13} /> 新任务</button>
                    </div>
                  ) : (
                    <form onSubmit={sendMessage}>
                      {selectedNode && <div className="agent-node-context"><GitBranch size={13} /> 正在讨论：{selectedNode.node.title}<button type="button" onClick={() => setSelectedNodeId(null)}><X size={12} /></button></div>}
                      <textarea
                        value={message}
                        onChange={(event) => setMessage(event.target.value)}
                        placeholder={proposal ? "可以继续说明修改要求；当前候选图仍需先决定。" : "描述调整、拆分、支线、约束或验收要求…"}
                      />
                      <div>
                        <small>每轮对话和候选图都会进入 revision 日志。</small>
                        <button className="button button-primary" disabled={busy || !message.trim()}>{busy ? <LoaderCircle size={14} /> : <Send size={14} />} 发送</button>
                      </div>
                    </form>
                  )}
                </div>
              </section>
            </aside>
          </div>
        </>
      )}
        </div>
        )}
      </main>
    </div>
  );
}
