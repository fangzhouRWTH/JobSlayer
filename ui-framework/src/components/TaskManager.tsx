import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
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
  ListChecks,
  ListTree,
  LoaderCircle,
  LockKeyhole,
  MessageSquareText,
  Network,
  Play,
  RefreshCw,
  Send,
  ShieldCheck,
  UserRound,
  X,
} from "lucide-react";
import type {
  ManagedNodeState,
  ManagedNodeView,
  ManagedTaskDetail,
  ManagedTaskStage,
  ManagedTaskSummary,
  TaskManagerSession,
  TaskPlanEdge,
} from "../types";

const API_ROOT = "/api/task-manager";

type MainTab = "dag" | "backlog" | "log";
type SideTab = "detail" | "agent";

type TaskNodeData = Record<string, unknown> & {
  title: string;
  kind: string;
  state: ManagedNodeState;
  dependencyCount: number;
  issueCount: number;
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
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { "managed-task": ManagedTaskNode };

function layoutGraph(
  nodes: ManagedNodeView[],
  edges: TaskPlanEdge[],
): { nodes: TaskGraphNode[]; edges: Edge[] } {
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
  const [session, setSession] = useState<TaskManagerSession | null>(null);
  const [tasks, setTasks] = useState<ManagedTaskSummary[]>([]);
  const [detail, setDetail] = useState<ManagedTaskDetail | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [mainTab, setMainTab] = useState<MainTab>("dag");
  const [sideTab, setSideTab] = useState<SideTab>("agent");
  const [showCreate, setShowCreate] = useState(false);
  const [taskDescription, setTaskDescription] = useState("");
  const [message, setMessage] = useState("");
  const [gateRationale, setGateRationale] = useState("");
  const [reviewRationale, setReviewRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    setSelectedNodeId((current) => next.nodes.some((item) => item.node.node_id === current) ? current : null);
    return next;
  };

  useEffect(() => {
    let cancelled = false;
    const start = async () => {
      try {
        const activeSession = await api<TaskManagerSession>("/session");
        if (cancelled) return;
        setSession(activeSession);
        const listing = await refreshTasks(activeSession);
        if (!cancelled && listing.length > 0) await loadTask(listing[0].task_id, activeSession);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      }
    };
    void start();
    return () => { cancelled = true; };
  }, []);

  const effectiveEdges = detail?.plan.pending_proposal?.edges ?? detail?.plan.edges ?? [];
  const graph = useMemo(
    () => layoutGraph(detail?.nodes ?? [], effectiveEdges),
    [detail, effectiveEdges],
  );
  const selectedNode = detail?.nodes.find((item) => item.node.node_id === selectedNodeId) ?? null;
  const selectedRunNode = detail?.execution_run?.nodes.find(
    (item) => item.node.node_id === selectedNodeId,
  ) ?? null;

  const commit = async (
    path: string,
    body: unknown,
    successMessage: string,
  ) => {
    if (!session) return;
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
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
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
      setTaskDescription("");
      setShowCreate(false);
      setMainTab("dag");
      setSideTab("agent");
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
    if (!detail || !message.trim()) return;
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

  const proposal = detail?.plan.pending_proposal ?? null;
  const canFinalize = Boolean(
    detail
      && detail.plan.status === "draft"
      && !proposal
      && detail.assessment.ready_to_finalize
      && detail.execution_target_assessment?.ready,
  );
  const selectedIsRootScopeGate = selectedNode?.node.kind === "human_gate"
    && selectedNode.dependency_node_ids.length === 0;
  const selectedIsCompletionGate = selectedNode?.node.kind === "human_gate"
    && selectedNode.dependency_node_ids.length > 0
    && !detail?.nodes.some((node) => node.dependency_node_ids.includes(selectedNode.node.node_id));
  const selectedExecutionCommand = selectedIsRootScopeGate && selectedNode?.state === "ready"
    ? "confirm-scope"
    : selectedIsCompletionGate && selectedNode?.state === "ready"
    ? "approve-completion"
    : selectedNode?.node.kind === "validation" && selectedNode?.state === "ready"
    ? "run-validation"
    : selectedNode?.state === "ready"
    ? "start"
    : selectedNode?.state === "running"
      ? "observe"
      : selectedNode?.state === "verifying" && selectedRunNode?.workflow_state === "verifying"
        ? "verify"
        : selectedNode?.state === "verifying" && selectedRunNode?.workflow_state === "reviewing"
          ? selectedRunNode.verification_report?.source_patch_sha256
            ? "review-source"
            : "accept-review"
        : selectedNode?.state === "verifying" && selectedRunNode?.workflow_state === "merge_review"
          ? "approve-checkpoint"
        : selectedNode?.state === "verifying" && selectedRunNode?.workflow_state === "integrating"
          ? "integrate-checkpoint"
      : selectedNode?.state === "failed" || selectedNode?.state === "blocked"
        ? "retry"
        : null;
  const selectedNodeUsesDedicatedPath = selectedNode?.node.kind === "human_gate"
    && !selectedIsRootScopeGate
    && !selectedIsCompletionGate;
  const selectedExecutionLabel = selectedExecutionCommand === "confirm-scope"
    ? "确认已固化范围"
    : selectedExecutionCommand === "approve-completion"
    ? "批准最终完成"
    : selectedNode?.node.kind === "human_gate"
    ? "等待授权人决定"
    : selectedNode?.node.kind === "validation"
      ? selectedExecutionCommand === "run-validation"
        ? "运行目标验证规则"
        : selectedExecutionCommand === "observe"
          ? "读取验证运行结果"
          : selectedExecutionCommand === "verify"
            ? "编译验证报告"
            : selectedExecutionCommand === "accept-review"
              ? "接受验证结果"
              : "等待确定性验证"
      : selectedExecutionCommand === "start"
    ? "授权执行"
    : selectedExecutionCommand === "observe"
      ? "刷新执行反馈"
      : selectedExecutionCommand === "verify"
        ? "运行确定性验证"
        : selectedExecutionCommand === "accept-review"
          ? "接受阶段性交付物"
        : selectedExecutionCommand === "review-source"
          ? "接受已验证源码补丁"
        : selectedExecutionCommand === "approve-checkpoint"
          ? "批准隔离分支检查点"
        : selectedExecutionCommand === "integrate-checkpoint"
          ? "写入隔离分支检查点"
      : selectedExecutionCommand === "retry"
        ? "授权重试"
        : "当前状态不可执行";
  const selectedCommandCapabilityAvailable = selectedExecutionCommand === "accept-review"
    ? Boolean(session?.capabilities.node_review)
    : selectedExecutionCommand === "review-source"
      ? Boolean(session?.capabilities.source_review)
    : selectedExecutionCommand === "approve-checkpoint"
      ? Boolean(session?.capabilities.source_checkpoint_approval)
    : selectedExecutionCommand === "integrate-checkpoint"
      ? Boolean(session?.capabilities.source_checkpoint_integration)
    : selectedExecutionCommand === "verify"
      ? Boolean(session?.capabilities.node_verification)
    : selectedExecutionCommand === "run-validation"
      ? Boolean(session?.capabilities.node_validation)
    : selectedExecutionCommand === "approve-completion"
      ? Boolean(session?.capabilities.completion_approval)
      : true;

  const runSelectedNode = () => {
    if (!detail?.execution_run || !selectedNode || !selectedExecutionCommand) return;
    void commit(
      `/tasks/${encodeURIComponent(detail.task.task_id)}/runs/${encodeURIComponent(detail.execution_run.run_id)}/nodes/${encodeURIComponent(selectedNode.node.node_id)}/${selectedExecutionCommand}`,
      selectedExecutionCommand === "confirm-scope"
        ? {
            expected_run_revision: detail.execution_run.revision,
            rationale: gateRationale.trim(),
          }
        : selectedExecutionCommand === "accept-review" || selectedExecutionCommand === "review-source" || selectedExecutionCommand === "approve-checkpoint" || selectedExecutionCommand === "approve-completion"
          ? {
              expected_run_revision: detail.execution_run.revision,
              rationale: reviewRationale.trim(),
            }
        : { expected_run_revision: detail.execution_run.revision },
      selectedExecutionCommand === "confirm-scope"
        ? "范围确认已绑定到 finalized revision；后续节点仍按依赖逐项授权。"
        : selectedExecutionCommand === "verify"
        ? "确定性验证事实和报告已登记；通过后仍等待 reviewer。"
        : selectedExecutionCommand === "run-validation"
        ? "已按 finalized target profile 运行受策略约束的本地验证；原始命令结果等待确定性报告编译。"
        : selectedExecutionCommand === "approve-completion"
        ? "独立 Approver 已依据最终验证与接受证据批准完成门禁；运行进入完成态。"
        : selectedExecutionCommand === "accept-review"
        ? "Reviewer 已接受无源码差异的阶段性交付物；依赖节点已按治理状态解锁。"
        : selectedExecutionCommand === "review-source"
        ? "Reviewer 已接受与验证报告一致的源码补丁；等待独立 Approver 决策。"
        : selectedExecutionCommand === "approve-checkpoint"
        ? "独立审批已固化；幂等检查点键已在任何 Git 副作用前写入任务事实链。"
        : selectedExecutionCommand === "integrate-checkpoint"
        ? "已将精确补丁提交到隔离运行分支；未合并主干、推送或部署。"
        : selectedExecutionCommand === "observe"
        ? "已同步外部执行反馈；状态只按证据推进。"
        : "执行授权已写入审计链；外部运行引用将使用幂等启动键绑定。",
    );
  };

  return (
    <div className="task-manager-page page-enter">
      <header className="task-manager-header">
        <div className="task-manager-heading">
          <span className="section-kicker"><Network size={13} /> FOCUSED PRODUCT LOOP</span>
          <h1>TaskManager</h1>
          <p>讨论、固化、追踪与反馈，共用一条可审计任务事实链。</p>
        </div>
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
          onClick={() => {
            if (!session) return;
            void refreshTasks(session).then((listing) => {
              if (detail) return loadTask(detail.task.task_id, session);
              if (listing[0]) return loadTask(listing[0].task_id, session);
              return undefined;
            }).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
          }}
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
            <textarea autoFocus value={taskDescription} onChange={(event) => setTaskDescription(event.target.value)} placeholder="例如：开发一个悬挂系统可视化案例，包含实时参数调节和验证说明。" />
            <div><button type="button" className="button button-quiet" onClick={() => setShowCreate(false)}>取消</button><button className="button button-primary" disabled={busy || !taskDescription.trim()}>{busy ? <LoaderCircle size={14} /> : <Bot size={14} />} 生成候选 DAG</button></div>
          </form>
        </section>
      )}

      {detail && !showCreate && (
        <>
          <div className="task-manager-statusbar">
            <span className={`task-stage stage-${detail.task.stage}`}><i /> {stageLabels[detail.task.stage]}</span>
            <span><strong>REV {detail.task.revision}</strong> · {detail.task.node_count} nodes · {detail.task.backlog_count} backlog</span>
            {detail.execution_run && <span><strong>RUN {detail.execution_run.run_id}</strong> · R{detail.execution_run.revision} · {detail.execution_run.stage.toUpperCase()}</span>}
            <span className="task-manager-truth"><ShieldCheck size={13} /> append-only · {detail.task.record_hash.slice(0, 10)}</span>
          </div>

          {proposal && (
            <div className="task-manager-proposal">
              <Bot size={17} />
              <div><strong>Agent 提交了候选 DAG</strong><span>{proposal.summary}</span></div>
              <button className="button button-quiet" disabled={busy} onClick={() => void commit(`/tasks/${encodeURIComponent(detail.task.task_id)}/proposal/reject`, { proposal_id: proposal.proposal_id, expected_revision: detail.task.revision }, "候选 DAG 已拒绝；已应用任务图保持不变。")}>拒绝</button>
              <button className="button button-primary" disabled={busy} onClick={() => void commit(`/tasks/${encodeURIComponent(detail.task.task_id)}/proposal/apply`, { proposal_id: proposal.proposal_id, expected_revision: detail.task.revision }, "候选 DAG 已应用为新的规划 revision。")}>{busy ? <LoaderCircle size={14} /> : <Check size={14} />} 应用候选图</button>
            </div>
          )}

          <div className="task-manager-layout">
            <section className="task-manager-main panel-surface">
              <nav className="task-manager-tabs" aria-label="任务主视图">
                <button className={mainTab === "dag" ? "active" : ""} onClick={() => setMainTab("dag")}><Network size={14} /> DAG</button>
                <button className={mainTab === "backlog" ? "active" : ""} onClick={() => setMainTab("backlog")}><ListChecks size={14} /> Backlog <i>{detail.backlog.length}</i></button>
                <button className={mainTab === "log" ? "active" : ""} onClick={() => setMainTab("log")}><Clock3 size={14} /> 总 Log <i>{detail.log.length}</i></button>
                <span>{proposal ? "PREVIEW · NOT APPLIED" : detail.plan.status.toUpperCase()}</span>
              </nav>

              {mainTab === "dag" && (
                <div className="task-manager-graph">
                  <ReactFlow
                    key={`${detail.task.task_id}-${detail.task.revision}-${detail.execution_run?.revision ?? 0}`}
                    nodes={graph.nodes}
                    edges={graph.edges}
                    nodeTypes={nodeTypes}
                    fitView
                    fitViewOptions={{ padding: 0.18, maxZoom: 1 }}
                    minZoom={0.25}
                    maxZoom={1.4}
                    nodesDraggable={false}
                    nodesConnectable={false}
                    elementsSelectable
                    onNodeClick={(_, node) => { setSelectedNodeId(node.id); setSideTab("detail"); }}
                    onPaneClick={() => setSelectedNodeId(null)}
                  >
                    <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#303640" />
                    <Controls showInteractive={false} />
                    <MiniMap pannable zoomable nodeColor={(node) => node.id === selectedNodeId ? "#b9ff66" : "#4d5663"} />
                  </ReactFlow>
                </div>
              )}

              {mainTab === "backlog" && (
                <div className="task-manager-backlog">
                  <div className="task-manager-list-head"><span>NODE</span><span>DEPENDENCIES</span><span>STATE / NEXT CONDITION</span></div>
                  {detail.backlog.map((item, index) => (
                    <button key={item.node_id} onClick={() => { setSelectedNodeId(item.node_id); setSideTab("detail"); }}>
                      <span className="task-manager-order">{String(index + 1).padStart(2, "0")}</span>
                      <span><strong>{item.title}</strong><small>{item.node_id}</small></span>
                      <span>{item.dependency_node_ids.length ? item.dependency_node_ids.join(", ") : "—"}</span>
                      <span><i className={`node-state state-${item.state}`}>{nodeStateLabels[item.state]}</i><small>{item.reason}</small></span>
                    </button>
                  ))}
                </div>
              )}

              {mainTab === "log" && (
                <div className="task-manager-log">
                  {[...detail.log].reverse().map((entry) => (
                    <article key={entry.log_id}>
                      <span className={`log-category category-${entry.category}`}>{entry.category}</span>
                      <div><strong>{entry.summary}</strong><small>{entry.event_type} · {entry.actor_type}:{entry.actor_id}</small></div>
                      <time>{formatTime(entry.occurred_at)}<small>rev {entry.revision}</small></time>
                    </article>
                  ))}
                </div>
              )}
            </section>

            <aside className="task-manager-side panel-surface">
              <nav className="task-manager-tabs" aria-label="任务信息面板">
                <button className={sideTab === "detail" ? "active" : ""} onClick={() => setSideTab("detail")}><ListTree size={14} /> 信息</button>
                <button className={sideTab === "agent" ? "active" : ""} onClick={() => setSideTab("agent")}><MessageSquareText size={14} /> Agent <i>{detail.plan.conversation.length}</i></button>
              </nav>

              {sideTab === "detail" && (
                <div className="task-manager-detail">
                  {selectedNode ? (
                    <>
                      <div className="detail-eyebrow">SELECTED NODE · {selectedNode.node.kind}</div>
                      <h2>{selectedNode.node.title}</h2>
                      <code>{selectedNode.node.node_id}</code>
                      <p>{selectedNode.node.description || "暂无节点描述。"}</p>
                      <dl><div><dt>状态</dt><dd><i className={`node-state state-${selectedNode.state}`}>{nodeStateLabels[selectedNode.state]}</i></dd></div><div><dt>执行建议</dt><dd>{selectedNode.node.executor_hint ?? "未指定"}</dd></div><div><dt>前置节点</dt><dd>{selectedNode.dependency_node_ids.join(", ") || "无"}</dd></div>{selectedRunNode && <div><dt>内核状态</dt><dd>{selectedRunNode.workflow_state} · transition {selectedRunNode.transition_history.length}</dd></div>}</dl>
                      <section><span>ACCEPTANCE CRITERIA</span>{selectedNode.node.acceptance_criteria.length ? selectedNode.node.acceptance_criteria.map((item) => <p key={item}><CheckCircle2 size={13} /> {item}</p>) : <p>尚未定义</p>}</section>
                      <section><span>VERIFICATION</span>{selectedNode.node.verification_requirements.length ? selectedNode.node.verification_requirements.map((item) => <p key={item}><ShieldCheck size={13} /> {item}</p>) : <p>尚未定义</p>}</section>
                      {selectedRunNode && <section><span>EXECUTION EVIDENCE</span><p><ShieldCheck size={13} /> workflow task: {selectedRunNode.workflow_task_id}</p>{selectedRunNode.provider_reference && <p><Bot size={13} /> {selectedRunNode.provider_reference.adapter_id} · {selectedRunNode.provider_reference.provider_run_id}</p>}{selectedRunNode.latest_observation && <p><Clock3 size={13} /> {selectedRunNode.latest_observation.summary}</p>}{selectedRunNode.verification_report && <p><ShieldCheck size={13} /> verification: {selectedRunNode.verification_report.required_checks_passed ? "PASSED" : "FAILED"} · {selectedRunNode.verification_report.checks.length} checks</p>}{selectedRunNode.verification_evidence && <p><ListChecks size={13} /> changed paths: {selectedRunNode.verification_evidence.workspace.changed_paths.length}</p>}{selectedRunNode.review_artifact_id && <p><CheckCircle2 size={13} /> reviewer evidence: {selectedRunNode.review_artifact_id}</p>}</section>}
                      {detail.execution_run && selectedExecutionCommand === "confirm-scope" && <label className="task-manager-gate-rationale"><span>范围确认理由</span><textarea value={gateRationale} onChange={(event) => setGateRationale(event.target.value)} placeholder="说明为何 finalized revision 已准确表达范围、契约与路径边界…" /></label>}
                      {detail.execution_run && ["accept-review", "review-source", "approve-checkpoint", "approve-completion"].includes(selectedExecutionCommand ?? "") && <label className="task-manager-gate-rationale"><span>{selectedExecutionCommand === "approve-checkpoint" ? "Approver 批准理由" : selectedExecutionCommand === "approve-completion" ? "最终验收理由" : "Reviewer 接受理由"}</span><textarea value={reviewRationale} onChange={(event) => setReviewRationale(event.target.value)} placeholder={selectedExecutionCommand === "approve-checkpoint" ? "说明为何该审查通过的补丁可写入隔离运行分支检查点…" : selectedExecutionCommand === "approve-completion" ? "说明已复核哪些最终验证、接受证据与发布边界…" : "说明已审阅哪些交付物、验收标准与验证证据…"} /></label>}
                      {detail.execution_run && <button className="button button-primary task-manager-run-node" disabled={busy || !detail.execution_available || !selectedExecutionCommand || selectedNodeUsesDedicatedPath || !selectedCommandCapabilityAvailable || (selectedExecutionCommand === "confirm-scope" && !gateRationale.trim()) || (["accept-review", "review-source", "approve-checkpoint", "approve-completion"].includes(selectedExecutionCommand ?? "") && !reviewRationale.trim())} title={!selectedCommandCapabilityAvailable ? "当前签名 session 缺少此操作所需权限。" : selectedNodeUsesDedicatedPath ? "该节点由专用治理路径处理，不会交给 Agent executor。" : detail.execution_blockers.join("\n")} onClick={runSelectedNode}>{busy ? <LoaderCircle size={14} /> : selectedExecutionCommand === "observe" ? <RefreshCw size={14} /> : ["confirm-scope", "accept-review", "review-source", "approve-checkpoint", "approve-completion"].includes(selectedExecutionCommand ?? "") ? <Check size={14} /> : selectedExecutionCommand === "verify" || selectedExecutionCommand === "integrate-checkpoint" ? <ShieldCheck size={14} /> : <Play size={14} />} {selectedExecutionLabel}</button>}
                      <button className="button button-quiet task-manager-discuss-node" onClick={() => setSideTab("agent")}><MessageSquareText size={14} /> 围绕此节点讨论</button>
                    </>
                  ) : (
                    <>
                      <div className="detail-eyebrow">TASK TRUTH · REV {detail.task.revision}</div>
                      <h2>{detail.task.title}</h2>
                      <p>{detail.task.task_description}</p>
                      <section className="task-manager-target">
                        <span>EXECUTION TARGET</span>
                        <select
                          aria-label="执行目标"
                          value={detail.plan.execution_target_id ?? ""}
                          disabled={busy || Boolean(proposal) || Boolean(detail.execution_run)}
                          onChange={(event) => {
                            if (!event.target.value) return;
                            void commit(
                              `/tasks/${encodeURIComponent(detail.task.task_id)}/target`,
                              {
                                target_id: event.target.value,
                                expected_revision: detail.task.revision,
                              },
                              "执行目标已写入任务 revision；目标预检已重新计算。",
                            );
                          }}
                        >
                          <option value="">选择固定执行目标…</option>
                          {detail.execution_targets.map((target) => (
                            <option key={target.target_id} value={target.target_id}>
                              {target.display_name}
                            </option>
                          ))}
                        </select>
                        {detail.execution_target && (
                          <div className="task-manager-target-facts">
                            <p><ShieldCheck size={13} /> {detail.execution_target.local_baseline_ready ? "bnw-0 本地基线已验证" : "本地基线未就绪"}</p>
                            <p><Bot size={13} /> {detail.execution_target.executor_model ?? detail.execution_target.model_profile} / {detail.execution_target.executor_reasoning_effort ?? "default"} · {Math.round(detail.execution_target.timeout_seconds / 3600)}h 上限</p>
                            <p><ListChecks size={13} /> {detail.execution_target.validation_commands.map((command) => command.join(" ")).join(" · ")}</p>
                            <code>{detail.execution_target.source_bundle_sha256.slice(0, 16)} · {detail.execution_target.baseline_commit.slice(0, 12)}</code>
                          </div>
                        )}
                        {detail.execution_target_assessment?.issues.map((issue) => (
                          <p key={`${issue.code}-${issue.node_id ?? "plan"}`}><AlertTriangle size={13} /> {issue.message}</p>
                        ))}
                      </section>
                      <dl><div><dt>计划状态</dt><dd>{stageLabels[detail.task.stage]}</dd></div><div><dt>完整度</dt><dd>{detail.assessment.ready_to_finalize ? "可固化" : `${detail.assessment.issues.length} 个问题`}</dd></div><div><dt>规划 Agent</dt><dd>{session?.agent_adapter ?? "unknown"}</dd></div>{detail.execution_run && <div><dt>执行运行</dt><dd>{detail.execution_run.run_id} · revision {detail.execution_run.revision}</dd></div>}</dl>
                      {detail.assessment.issues.length > 0 && <section><span>PLAN ISSUES</span>{detail.assessment.issues.map((issue) => <p key={`${issue.code}-${issue.node_id}`}><AlertTriangle size={13} /> {issue.message}</p>)}</section>}
                      <section className="execution-boundary"><span>EXECUTION BOUNDARY</span>{detail.execution_blockers.length ? detail.execution_blockers.map((blocker) => <p key={blocker}><LockKeyhole size={13} /> {blocker}</p>) : <p><ShieldCheck size={13} /> 执行 adapter 已连接；仍需逐节点授权并提交证据。</p>}</section>
                      <div className="task-manager-actions">
                        <button className="button button-primary" disabled={!canFinalize || busy} onClick={() => void commit(`/tasks/${encodeURIComponent(detail.task.task_id)}/finalize`, { expected_revision: detail.task.revision }, "任务流已固化；后续执行必须绑定该 revision。")}>{busy ? <LoaderCircle size={14} /> : <LockKeyhole size={14} />} 固化任务流</button>
                        <button className="button button-quiet" disabled={!detail.run_assembly_available || busy} title={detail.execution_blockers.join("\n")} onClick={() => void commit(`/tasks/${encodeURIComponent(detail.task.task_id)}/runs`, { expected_revision: detail.task.revision }, "已创建与 finalized revision 精确绑定的执行运行。")}>{busy ? <LoaderCircle size={14} /> : <Play size={14} />} {detail.execution_run ? "运行已创建" : "创建执行运行"}</button>
                      </div>
                    </>
                  )}
                </div>
              )}

              {sideTab === "agent" && (
                <div className="task-manager-agent">
                  <div className="task-manager-conversation">
                    {detail.plan.conversation.map((entry) => (
                      <article key={entry.message_id} className={entry.role}>
                        <span>{entry.role === "user" ? <UserRound size={14} /> : <Bot size={14} />}</span>
                        <div><strong>{entry.role === "user" ? session?.principal.display_name ?? "User" : entry.agent_adapter ?? "TaskManager"}</strong><p>{entry.content}</p><time>{formatTime(entry.created_at)}</time></div>
                      </article>
                    ))}
                  </div>
                  <form onSubmit={sendMessage}>
                    {selectedNode && <div className="agent-node-context"><GitBranch size={13} /> 正在讨论：{selectedNode.node.title}<button type="button" onClick={() => setSelectedNodeId(null)}><X size={12} /></button></div>}
                    <textarea value={message} onChange={(event) => setMessage(event.target.value)} placeholder={proposal ? "可以继续说明修改要求；当前候选图仍需先决定。" : "描述调整、拆分、支线、约束或验收要求…"} />
                    <div><small>每轮对话和候选图都会进入 revision 日志。</small><button className="button button-primary" disabled={busy || !message.trim()}>{busy ? <LoaderCircle size={14} /> : <Send size={14} />} 发送</button></div>
                  </form>
                </div>
              )}
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
