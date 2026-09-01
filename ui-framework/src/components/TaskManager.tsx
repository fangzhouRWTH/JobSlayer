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
  ManagedNodeState,
  ManagedNodeView,
  ManagedTaskDetail,
  ManagedTaskStage,
  ManagedTaskSummary,
  TaskManagerSession,
  TaskPlanEdge,
} from "../types";

const API_ROOT = "/api/task-manager";

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
  const [showCreate, setShowCreate] = useState(false);
  const [taskDescription, setTaskDescription] = useState("");
  const [message, setMessage] = useState("");
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

  return (
    <div className="task-manager-page page-enter">
      <header className="task-manager-header">
        <div className="task-manager-heading">
          <h1>Task Graph</h1>
          <p>预览节点、检查细节、与 Agent 调整任务。</p>
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

          <div className="task-manager-layout">
            <section className="task-manager-main panel-surface">
              <header className="task-manager-panel-title">
                <span><Network size={14} /> TASK GRAPH</span>
                <small>{proposal ? "PREVIEW · NOT APPLIED" : detail.plan.status.toUpperCase()}</small>
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
                  <form onSubmit={sendMessage}>
                    {selectedNode && <div className="agent-node-context"><GitBranch size={13} /> 正在讨论：{selectedNode.node.title}<button type="button" onClick={() => setSelectedNodeId(null)}><X size={12} /></button></div>}
                    <textarea value={message} onChange={(event) => setMessage(event.target.value)} placeholder={proposal ? "可以继续说明修改要求；当前候选图仍需先决定。" : "描述调整、拆分、支线、约束或验收要求…"} />
                    <div><small>每轮对话和候选图都会进入 revision 日志。</small><button className="button button-primary" disabled={busy || !message.trim()}>{busy ? <LoaderCircle size={14} /> : <Send size={14} />} 发送</button></div>
                  </form>
                </div>
              </section>
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
