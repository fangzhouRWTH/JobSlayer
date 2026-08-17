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
  Bot,
  Check,
  CirclePlus,
  GitBranch,
  ListTree,
  LockKeyhole,
  MessageSquareText,
  Network,
  Save,
  Send,
  ShieldCheck,
  Split,
  Trash2,
  UserRound,
} from "lucide-react";
import type {
  TaskPlanEdge,
  TaskPlanEdgeRelation,
  TaskPlanNode,
  TaskPlanNodeKind,
  TaskPlanRevisionRecord,
} from "../types";

const API_ROOT = "/api/orchestration";

interface OrchestrationSession {
  principal: { subject_id: string; display_name: string; roles: string[] };
  agent_adapter: string;
  submission_token: string;
  capabilities: { workflow_execution: boolean };
}

type PlanGraphData = Record<string, unknown> & {
  title: string;
  kind: TaskPlanNodeKind;
  executor: string;
  pending: boolean;
};

type PlanGraphNode = Node<PlanGraphData, "plan-step">;

const kindLabels: Record<TaskPlanNodeKind, string> = {
  task: "TASK",
  milestone: "MILESTONE",
  validation: "VALIDATION",
  human_gate: "HUMAN GATE",
};

const relationColors: Record<TaskPlanEdgeRelation, string> = {
  sequence: "#78818d",
  dependency: "#8995ff",
  branch: "#f2c86b",
  subtask: "#b9ff66",
};

function PlanNodeCard({ data, selected }: NodeProps<PlanGraphNode>) {
  const Icon = data.kind === "human_gate" ? LockKeyhole : data.kind === "validation" ? ShieldCheck : data.kind === "milestone" ? GitBranch : ListTree;
  return (
    <div className={`plan-flow-node ${data.pending ? "pending" : "applied"} ${selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="plan-node-meta"><span><Icon size={13} /> {kindLabels[data.kind]}</span><i>{data.pending ? "PROPOSED" : "APPLIED"}</i></div>
      <strong>{data.title}</strong>
      <small>{data.executor}</small>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

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

function graphLayout(
  planNodes: TaskPlanNode[],
  planEdges: TaskPlanEdge[],
  pending: boolean,
): { nodes: PlanGraphNode[]; edges: Edge[] } {
  const depth = new Map(planNodes.map((node) => [node.node_id, 0]));
  for (let pass = 0; pass < planNodes.length; pass += 1) {
    for (const edge of planEdges) {
      const sourceDepth = depth.get(edge.source_node_id) ?? 0;
      const targetDepth = depth.get(edge.target_node_id) ?? 0;
      if (targetDepth <= sourceDepth) depth.set(edge.target_node_id, sourceDepth + 1);
    }
  }
  const rows = new Map<number, number>();
  const nodes: PlanGraphNode[] = planNodes.map((node) => {
    const column = depth.get(node.node_id) ?? 0;
    const row = rows.get(column) ?? 0;
    rows.set(column, row + 1);
    return {
      id: node.node_id,
      type: "plan-step",
      position: { x: 55 + column * 238, y: 55 + row * 142 },
      data: {
        title: node.title,
        kind: node.kind,
        executor: node.executor_hint ?? "unassigned",
        pending,
      },
    };
  });
  const edges: Edge[] = planEdges.map((edge) => ({
    id: edge.edge_id,
    source: edge.source_node_id,
    target: edge.target_node_id,
    label: edge.relation,
    markerEnd: { type: MarkerType.ArrowClosed, color: relationColors[edge.relation] },
    style: {
      stroke: relationColors[edge.relation],
      strokeDasharray: pending ? "5 5" : undefined,
    },
    labelStyle: { fill: relationColors[edge.relation], fontSize: 8 },
    labelBgStyle: { fill: "#0d1014", fillOpacity: 0.92 },
  }));
  return { nodes, edges };
}

interface TaskOrchestrationProps {
  onNotice: (message: string) => void;
}

export function TaskOrchestration({ onNotice }: TaskOrchestrationProps) {
  const [session, setSession] = useState<OrchestrationSession | null>(null);
  const [record, setRecord] = useState<TaskPlanRevisionRecord | null>(null);
  const [history, setHistory] = useState<TaskPlanRevisionRecord[]>([]);
  const [taskDescription, setTaskDescription] = useState("");
  const [discussion, setDiscussion] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editKind, setEditKind] = useState<TaskPlanNodeKind>("task");
  const [editExecutor, setEditExecutor] = useState("");
  const [childTitle, setChildTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const connect = async () => {
      try {
        const nextSession = await api<OrchestrationSession>("/session");
        const listing = await api<{ plans: TaskPlanRevisionRecord[] }>("/plans");
        if (!active) return;
        setSession(nextSession);
        const latest = [...listing.plans].sort((left, right) => left.occurred_at.localeCompare(right.occurred_at)).at(-1) ?? null;
        setRecord(latest);
        if (latest) {
          const result = await api<{ history: TaskPlanRevisionRecord[] }>(`/plans/${encodeURIComponent(latest.plan_id)}/history`);
          if (active) setHistory(result.history);
        }
      } catch (caught) {
        if (active) setError(caught instanceof Error ? caught.message : "任务编排 API 不可用");
      }
    };
    void connect();
    return () => { active = false; };
  }, []);

  const snapshot = record?.snapshot ?? null;
  const effectiveNodes = snapshot?.pending_proposal?.nodes ?? snapshot?.nodes ?? [];
  const effectiveEdges = snapshot?.pending_proposal?.edges ?? snapshot?.edges ?? [];
  const pending = Boolean(snapshot?.pending_proposal);
  const graph = useMemo(
    () => graphLayout(effectiveNodes, effectiveEdges, pending),
    [effectiveNodes, effectiveEdges, pending],
  );
  const nodeTypes = useMemo(() => ({ "plan-step": PlanNodeCard }), []);
  const selected = effectiveNodes.find((node) => node.node_id === selectedId) ?? null;

  useEffect(() => {
    if (!effectiveNodes.length) {
      setSelectedId(null);
      return;
    }
    if (!effectiveNodes.some((node) => node.node_id === selectedId)) setSelectedId(effectiveNodes[0].node_id);
  }, [effectiveNodes, selectedId]);

  useEffect(() => {
    if (!selected) return;
    setEditTitle(selected.title);
    setEditDescription(selected.description);
    setEditKind(selected.kind);
    setEditExecutor(selected.executor_hint ?? "");
  }, [selected]);

  const refreshHistory = async (planId: string) => {
    const result = await api<{ history: TaskPlanRevisionRecord[] }>(`/plans/${encodeURIComponent(planId)}/history`);
    setHistory(result.history);
  };

  const accept = async (next: TaskPlanRevisionRecord, notice: string) => {
    setRecord(next);
    await refreshHistory(next.plan_id);
    setError(null);
    onNotice(notice);
  };

  const mutate = async (
    method: string,
    path: string,
    body: unknown,
    notice: string,
  ) => {
    if (!session) return;
    setBusy(true);
    try {
      const next = await api<TaskPlanRevisionRecord>(path, { method, token: session.submission_token, body });
      await accept(next, notice);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "编排命令失败");
    } finally {
      setBusy(false);
    }
  };

  const createPlan = async (event: FormEvent) => {
    event.preventDefault();
    if (!taskDescription.trim() || !session) return;
    await mutate("POST", "/plans", { task_description: taskDescription }, "已创建版本化计划，并记录 Agent 的首个待应用提案。");
  };

  const sendDiscussion = async (event: FormEvent) => {
    event.preventDefault();
    if (!snapshot || !discussion.trim()) return;
    const content = discussion;
    setDiscussion("");
    await mutate(
      "POST",
      `/plans/${encodeURIComponent(snapshot.plan_id)}/messages`,
      { content, expected_revision: snapshot.revision, selected_node_id: selectedId },
      "新一轮讨论已记录；拓扑显示新的待应用提案。",
    );
  };

  const applyProposal = async () => {
    if (!snapshot?.pending_proposal) return;
    await mutate(
      "POST",
      `/plans/${encodeURIComponent(snapshot.plan_id)}/proposals/apply`,
      { proposal_id: snapshot.pending_proposal.proposal_id, expected_revision: snapshot.revision },
      "用户已应用 Agent 提案，计划图进入新的已应用 revision。",
    );
  };

  const finalizePlan = async () => {
    if (!snapshot) return;
    await mutate(
      "POST",
      `/plans/${encodeURIComponent(snapshot.plan_id)}/finalize`,
      { expected_revision: snapshot.revision },
      "最终任务路径已固化为追加式、可验哈希的 revision。",
    );
  };

  const saveNode = async () => {
    if (!snapshot || !selected) return;
    await mutate(
      "PATCH",
      `/plans/${encodeURIComponent(snapshot.plan_id)}/nodes/${encodeURIComponent(selected.node_id)}`,
      {
        title: editTitle,
        description: editDescription,
        kind: editKind,
        executor_hint: editExecutor.trim() || null,
        expected_revision: snapshot.revision,
      },
      `节点 ${selected.node_id} 已写入新 revision。`,
    );
  };

  const deleteNode = async () => {
    if (!snapshot || !selected) return;
    await mutate(
      "DELETE",
      `/plans/${encodeURIComponent(snapshot.plan_id)}/nodes/${encodeURIComponent(selected.node_id)}`,
      { expected_revision: snapshot.revision },
      `节点 ${selected.node_id} 及关联边已移除。`,
    );
  };

  const addNode = async (relation?: "branch" | "subtask") => {
    if (!snapshot || !childTitle.trim()) return;
    const path = relation && selected
      ? `/plans/${encodeURIComponent(snapshot.plan_id)}/nodes/${encodeURIComponent(selected.node_id)}/split`
      : `/plans/${encodeURIComponent(snapshot.plan_id)}/nodes`;
    const body = relation && selected
      ? { title: childTitle, description: "", relation, expected_revision: snapshot.revision }
      : { title: childTitle, description: "", expected_revision: snapshot.revision };
    await mutate("POST", path, body, relation ? `已创建${relation === "branch" ? "支线" : "子任务"}节点。` : "已创建独立任务节点。");
    setChildTitle("");
  };

  return (
    <div className="workbench-page page-enter orchestration-page">
      <header className="page-titlebar">
        <div>
          <span className="section-index">TASK ORCHESTRATION / {snapshot?.plan_id ?? "NEW PLAN"}</span>
          <h1><Network size={23} /> Task Orchestration</h1>
        </div>
        <div className="page-actions">
          {snapshot?.pending_proposal && <button className="button button-quiet" disabled={busy} onClick={applyProposal}><Check size={15} /> 应用提案</button>}
          <button className="button button-primary" disabled={busy || !snapshot || pending || !snapshot.nodes.length || snapshot.status === "finalized"} onClick={finalizePlan}><LockKeyhole size={15} /> 固化最终路径</button>
        </div>
      </header>

      <div className={`orchestration-banner ${error ? "offline" : ""}`}>
        <ShieldCheck size={15} />
        <span><strong>{error ? "API OFFLINE" : "VERSIONED PLAN CONTROL"}</strong>{error ?? (snapshot ? `revision ${snapshot.revision} · ${pending ? "AGENT PROPOSAL PENDING" : snapshot.status.toUpperCase()} · execution disabled` : "输入任务后由系统创建首个计划 revision")}</span>
        {record && <code>{record.record_hash.slice(0, 12)}</code>}
      </div>

      {!record || !snapshot ? (
        <section className="orchestration-start panel-surface">
          <div><MessageSquareText size={28} /><span>01 / DESCRIBE</span><h2>先描述你想完成的任务</h2><p>系统会保存输入，并让本地提案器给出第一版阶段拓扑。Agent 的结果只是 proposal，必须由用户应用。</p></div>
          <form onSubmit={createPlan}>
            <label htmlFor="task-description">任务描述</label>
            <textarea id="task-description" value={taskDescription} onChange={(event) => setTaskDescription(event.target.value)} placeholder="例如：设计并实现一个可扩展的任务编排框架，支持多轮讨论、分支和版本化固化……" rows={8} />
            <button className="button button-primary" disabled={!session || busy || !taskDescription.trim()}><CirclePlus size={15} /> 创建任务编排</button>
            {!session && <small>先启动 `jobslayer orchestration-api`；Vite 会把此页面的 API 请求代理到 127.0.0.1:8780。</small>}
          </form>
        </section>
      ) : (
        <div className="orchestration-grid">
          <aside className="discussion-panel panel-surface">
            <div className="panel-label">DISCUSSION · {snapshot.conversation.length} MESSAGES</div>
            <div className="discussion-thread">
              {snapshot.conversation.map((message) => (
                <article key={message.message_id} className={`discussion-message ${message.role}`}>
                  <span>{message.role === "agent" ? <Bot size={14} /> : <UserRound size={14} />}{message.role === "agent" ? message.agent_adapter : session?.principal.display_name ?? "User"}</span>
                  <p>{message.content}</p>
                  <time>{new Date(message.created_at).toLocaleTimeString()}</time>
                </article>
              ))}
            </div>
            <form className="discussion-compose" onSubmit={sendDiscussion}>
              <textarea value={discussion} onChange={(event) => setDiscussion(event.target.value)} placeholder={selected ? `围绕“${selected.title}”继续讨论；可说明修改、删除、支线、子任务或依赖…` : "继续细化任务…"} rows={4} />
              <button className="button button-primary" disabled={busy || !discussion.trim()}><Send size={14} /> 发送并生成提案</button>
            </form>
          </aside>

          <section className="orchestration-canvas panel-surface">
            <div className="canvas-tabs">
              <button className="active">Plan topology</button>
              <span>{effectiveNodes.length} nodes · {effectiveEdges.length} edges · {pending ? "proposal view" : "applied view"}</span>
            </div>
            <div className="flow-canvas">
              <ReactFlow
                key={`${snapshot.plan_id}-${snapshot.revision}-${snapshot.pending_proposal?.proposal_id ?? "applied"}`}
                nodes={graph.nodes}
                edges={graph.edges}
                nodeTypes={nodeTypes}
                onNodeClick={(_, node) => setSelectedId(node.id)}
                nodesDraggable={false}
                nodesConnectable={false}
                elementsSelectable
                fitView
                minZoom={0.45}
                maxZoom={1.6}
                aria-label="版本化任务计划拓扑"
              >
                <Background variant={BackgroundVariant.Dots} color="#30343d" gap={22} size={1} />
                <MiniMap nodeColor={(node) => node.id === selectedId ? "#b9ff66" : pending ? "#f2c86b" : "#58616d"} maskColor="rgba(10,12,15,.75)" />
                <Controls showInteractive={false} />
              </ReactFlow>
            </div>
            {snapshot.pending_proposal && <div className="proposal-strip"><Bot size={14} /><span><strong>待应用提案</strong>{snapshot.pending_proposal.summary}</span><button onClick={applyProposal} disabled={busy}>应用到计划</button></div>}
          </section>

          <aside className="orchestration-inspector panel-surface">
            <div className="panel-label">NODE CRUD / REVISION</div>
            {selected ? (
              <div className="node-editor">
                <div className="selected-node-title"><span><ListTree size={15} /></span><div><strong>{selected.title}</strong><small>{selected.node_id}</small></div></div>
                <label>标题<input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} disabled={pending} /></label>
                <label>详细描述<textarea value={editDescription} onChange={(event) => setEditDescription(event.target.value)} rows={5} disabled={pending} /></label>
                <label>节点类型<select value={editKind} onChange={(event) => setEditKind(event.target.value as TaskPlanNodeKind)} disabled={pending}><option value="task">Task</option><option value="milestone">Milestone</option><option value="validation">Validation</option><option value="human_gate">Human gate</option></select></label>
                <label>执行提示<input value={editExecutor} onChange={(event) => setEditExecutor(event.target.value)} disabled={pending} placeholder="provider-neutral hint" /></label>
                <div className="node-editor-actions"><button className="button button-quiet" onClick={saveNode} disabled={busy || pending || !editTitle.trim()}><Save size={14} /> 保存</button><button className="button button-danger-quiet" onClick={deleteNode} disabled={busy || pending}><Trash2 size={14} /> 删除</button></div>
              </div>
            ) : <p className="empty-inspector">选择一个节点查看和修改。</p>}

            <div className="split-editor">
              <span>CREATE / SPLIT</span>
              <input value={childTitle} onChange={(event) => setChildTitle(event.target.value)} placeholder="新节点标题" disabled={pending} />
              <div><button onClick={() => addNode()} disabled={busy || pending || !childTitle.trim()}><CirclePlus size={13} /> 独立节点</button><button onClick={() => addNode("branch")} disabled={busy || pending || !selected || !childTitle.trim()}><GitBranch size={13} /> 支线</button><button onClick={() => addNode("subtask")} disabled={busy || pending || !selected || !childTitle.trim()}><Split size={13} /> 子任务</button></div>
            </div>

            <div className="revision-list">
              <span>APPEND-ONLY HISTORY</span>
              {[...history].reverse().slice(0, 7).map((item) => <div key={item.record_id}><b>v{item.sequence}</b><p>{item.operation}<small>{item.record_hash.slice(0, 12)}</small></p></div>)}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
