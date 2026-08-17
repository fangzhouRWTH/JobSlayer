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
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import {
  AlertTriangle,
  Archive,
  Bot,
  Check,
  CirclePlus,
  GitBranch,
  GitCompare,
  Info,
  Link2,
  ListTree,
  LockKeyhole,
  MessageSquareText,
  Network,
  RotateCcw,
  Save,
  Search,
  Send,
  ShieldCheck,
  Split,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { diffPlanGraphs, joinLines, splitLines } from "../taskPlan";
import type {
  TaskPlanAssessment,
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
  capabilities: {
    workflow_execution: boolean;
    edge_crud: boolean;
    revision_derivation: boolean;
    completeness_assessment: boolean;
  };
}

type PlanGraphData = Record<string, unknown> & {
  title: string;
  kind: TaskPlanNodeKind;
  executor: string;
  pending: boolean;
  issueCount: number;
};

type PlanGraphNode = Node<PlanGraphData, "plan-step">;
type LayoutPositions = Record<string, { x: number; y: number }>;

const kindLabels: Record<TaskPlanNodeKind, string> = {
  task: "TASK",
  milestone: "MILESTONE",
  validation: "VALIDATION",
  human_gate: "HUMAN GATE",
};

const relationLabels: Record<TaskPlanEdgeRelation, string> = {
  sequence: "顺序",
  dependency: "依赖",
  branch: "支线",
  subtask: "子任务",
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
      <div className="plan-node-meta">
        <span><Icon size={13} /> {kindLabels[data.kind]}</span>
        <i>{data.issueCount ? `${data.issueCount} ISSUE${data.issueCount > 1 ? "S" : ""}` : data.pending ? "PROPOSED" : "READY"}</i>
      </div>
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

function layoutKey(planId: string): string {
  return `jobslayer.plan-layout.${planId}`;
}

function readLayout(planId: string): LayoutPositions {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(layoutKey(planId)) ?? "{}");
    return typeof parsed === "object" && parsed !== null ? parsed as LayoutPositions : {};
  } catch {
    return {};
  }
}

function graphLayout(
  planNodes: TaskPlanNode[],
  planEdges: TaskPlanEdge[],
  pending: boolean,
  layout: LayoutPositions,
  assessment: TaskPlanAssessment | null,
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
      position: layout[node.node_id] ?? { x: 55 + column * 238, y: 55 + row * 142 },
      data: {
        title: node.title,
        kind: node.kind,
        executor: node.executor_hint ?? "unassigned",
        pending,
        issueCount: assessment?.issues.filter((issue) => issue.node_id === node.node_id).length ?? 0,
      },
    };
  });
  const edges: Edge[] = planEdges.map((edge) => ({
    id: edge.edge_id,
    source: edge.source_node_id,
    target: edge.target_node_id,
    label: edge.label ? `${relationLabels[edge.relation]} · ${edge.label}` : relationLabels[edge.relation],
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
  const [plans, setPlans] = useState<TaskPlanRevisionRecord[]>([]);
  const [record, setRecord] = useState<TaskPlanRevisionRecord | null>(null);
  const [history, setHistory] = useState<TaskPlanRevisionRecord[]>([]);
  const [assessment, setAssessment] = useState<TaskPlanAssessment | null>(null);
  const [layoutPositions, setLayoutPositions] = useState<LayoutPositions>({});
  const [taskDescription, setTaskDescription] = useState("");
  const [discussion, setDiscussion] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editKind, setEditKind] = useState<TaskPlanNodeKind>("task");
  const [editExecutor, setEditExecutor] = useState("");
  const [editAcceptance, setEditAcceptance] = useState("");
  const [editDeliverables, setEditDeliverables] = useState("");
  const [editConstraints, setEditConstraints] = useState("");
  const [editRisks, setEditRisks] = useState("");
  const [editVerification, setEditVerification] = useState("");
  const [editHumanDecision, setEditHumanDecision] = useState(false);
  const [editEdgeRelation, setEditEdgeRelation] = useState<TaskPlanEdgeRelation>("sequence");
  const [editEdgeLabel, setEditEdgeLabel] = useState("");
  const [newEdgeRelation, setNewEdgeRelation] = useState<TaskPlanEdgeRelation>("sequence");
  const [childTitle, setChildTitle] = useState("");
  const [planSearch, setPlanSearch] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [showPlanManager, setShowPlanManager] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [compareRevision, setCompareRevision] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshPlans = async () => {
    const listing = await api<{ plans: TaskPlanRevisionRecord[] }>("/plans");
    setPlans(listing.plans);
    return listing.plans;
  };

  const loadPlan = async (planId: string) => {
    const [next, result, nextAssessment] = await Promise.all([
      api<TaskPlanRevisionRecord>(`/plans/${encodeURIComponent(planId)}`),
      api<{ history: TaskPlanRevisionRecord[] }>(`/plans/${encodeURIComponent(planId)}/history`),
      api<TaskPlanAssessment>(`/plans/${encodeURIComponent(planId)}/assessment`),
    ]);
    setLayoutPositions(readLayout(planId));
    setRecord(next);
    setHistory(result.history);
    setAssessment(nextAssessment);
    setSelectedId(null);
    setSelectedEdgeId(null);
    setCompareRevision(result.history.at(-2)?.sequence ?? result.history.at(-1)?.sequence ?? null);
    setShowPlanManager(false);
    setError(null);
  };

  useEffect(() => {
    let active = true;
    const connect = async () => {
      try {
        const nextSession = await api<OrchestrationSession>("/session");
        const listing = await api<{ plans: TaskPlanRevisionRecord[] }>("/plans");
        if (!active) return;
        setSession(nextSession);
        setPlans(listing.plans);
        const latest = [...listing.plans]
          .filter((item) => !item.snapshot.is_archived)
          .sort((left, right) => left.occurred_at.localeCompare(right.occurred_at))
          .at(-1) ?? [...listing.plans].sort((left, right) => left.occurred_at.localeCompare(right.occurred_at)).at(-1) ?? null;
        if (latest) await loadPlan(latest.plan_id);
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
    () => graphLayout(effectiveNodes, effectiveEdges, pending, layoutPositions, assessment),
    [effectiveNodes, effectiveEdges, pending, layoutPositions, assessment],
  );
  const nodeTypes = useMemo(() => ({ "plan-step": PlanNodeCard }), []);
  const selected = effectiveNodes.find((node) => node.node_id === selectedId) ?? null;
  const selectedEdge = effectiveEdges.find((edge) => edge.edge_id === selectedEdgeId) ?? null;
  const proposalDiff = snapshot?.pending_proposal
    ? diffPlanGraphs(snapshot.nodes, snapshot.edges, snapshot.pending_proposal.nodes, snapshot.pending_proposal.edges)
    : null;
  const compareRecord = history.find((item) => item.sequence === compareRevision) ?? null;
  const revisionDiff = snapshot && compareRecord
    ? diffPlanGraphs(compareRecord.snapshot.nodes, compareRecord.snapshot.edges, snapshot.nodes, snapshot.edges)
    : null;
  const filteredPlans = plans
    .filter((item) => showArchived || !item.snapshot.is_archived)
    .filter((item) => `${item.plan_id} ${item.snapshot.task_description}`.toLowerCase().includes(planSearch.trim().toLowerCase()))
    .sort((left, right) => right.occurred_at.localeCompare(left.occurred_at));
  const blockerCount = assessment?.issues.filter((issue) => issue.severity === "blocker").length ?? 0;
  const warningCount = assessment?.issues.filter((issue) => issue.severity === "warning").length ?? 0;

  useEffect(() => {
    if (selectedEdgeId) return;
    if (!effectiveNodes.length) {
      setSelectedId(null);
      return;
    }
    if (!effectiveNodes.some((node) => node.node_id === selectedId)) setSelectedId(effectiveNodes[0].node_id);
  }, [effectiveNodes, selectedId, selectedEdgeId]);

  useEffect(() => {
    if (!selected) return;
    setEditTitle(selected.title);
    setEditDescription(selected.description);
    setEditKind(selected.kind);
    setEditExecutor(selected.executor_hint ?? "");
    setEditAcceptance(joinLines(selected.acceptance_criteria));
    setEditDeliverables(joinLines(selected.deliverables));
    setEditConstraints(joinLines(selected.constraints));
    setEditRisks(joinLines(selected.risks));
    setEditVerification(joinLines(selected.verification_requirements));
    setEditHumanDecision(selected.requires_human_decision);
  }, [selected]);

  useEffect(() => {
    if (!selectedEdge) return;
    setEditEdgeRelation(selectedEdge.relation);
    setEditEdgeLabel(selectedEdge.label ?? "");
  }, [selectedEdge]);

  const accept = async (next: TaskPlanRevisionRecord, notice: string) => {
    const [result, nextAssessment] = await Promise.all([
      api<{ history: TaskPlanRevisionRecord[] }>(`/plans/${encodeURIComponent(next.plan_id)}/history`),
      api<TaskPlanAssessment>(`/plans/${encodeURIComponent(next.plan_id)}/assessment`),
      refreshPlans(),
    ]);
    setRecord(next);
    setHistory(result.history);
    setAssessment(nextAssessment);
    setCompareRevision((current) => current ?? result.history.at(-2)?.sequence ?? null);
    setError(null);
    onNotice(notice);
  };

  const mutate = async (
    method: string,
    path: string,
    body: unknown,
    notice: string,
  ): Promise<TaskPlanRevisionRecord | null> => {
    if (!session) return null;
    setBusy(true);
    try {
      const next = await api<TaskPlanRevisionRecord>(path, { method, token: session.submission_token, body });
      await accept(next, notice);
      return next;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "编排命令失败");
      return null;
    } finally {
      setBusy(false);
    }
  };

  const createPlan = async (event: FormEvent) => {
    event.preventDefault();
    if (!taskDescription.trim() || !session) return;
    const next = await mutate("POST", "/plans", { task_description: taskDescription }, "已创建版本化计划，并记录 Agent 的首个待应用提案。");
    if (next) {
      setTaskDescription("");
      setShowCreate(false);
      setShowPlanManager(false);
      setLayoutPositions({});
    }
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

  const rejectProposal = async () => {
    if (!snapshot?.pending_proposal) return;
    await mutate(
      "POST",
      `/plans/${encodeURIComponent(snapshot.plan_id)}/proposals/reject`,
      { proposal_id: snapshot.pending_proposal.proposal_id, expected_revision: snapshot.revision },
      "用户已拒绝 Agent 提案；已应用计划图保持不变。",
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

  const toggleArchive = async () => {
    if (!snapshot) return;
    await mutate(
      "POST",
      `/plans/${encodeURIComponent(snapshot.plan_id)}/archive`,
      { archived: !snapshot.is_archived, expected_revision: snapshot.revision },
      snapshot.is_archived ? "计划已恢复为活动草案。" : "计划已归档并切换为只读。",
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
        acceptance_criteria: splitLines(editAcceptance),
        deliverables: splitLines(editDeliverables),
        constraints: splitLines(editConstraints),
        risks: splitLines(editRisks),
        verification_requirements: splitLines(editVerification),
        requires_human_decision: editHumanDecision,
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
    const next = await mutate("POST", path, body, relation ? `已创建${relation === "branch" ? "支线" : "子任务"}节点。` : "已创建独立任务节点。");
    if (next) setChildTitle("");
  };

  const createEdge = async (connection: Connection) => {
    if (!snapshot || !connection.source || !connection.target || pending || snapshot.is_archived) return;
    await mutate(
      "POST",
      `/plans/${encodeURIComponent(snapshot.plan_id)}/edges`,
      {
        source_node_id: connection.source,
        target_node_id: connection.target,
        relation: newEdgeRelation,
        expected_revision: snapshot.revision,
      },
      `已创建${relationLabels[newEdgeRelation]}关系。`,
    );
  };

  const saveEdge = async () => {
    if (!snapshot || !selectedEdge) return;
    await mutate(
      "PATCH",
      `/plans/${encodeURIComponent(snapshot.plan_id)}/edges/${encodeURIComponent(selectedEdge.edge_id)}`,
      { relation: editEdgeRelation, label: editEdgeLabel.trim() || null, expected_revision: snapshot.revision },
      `关系 ${selectedEdge.edge_id} 已更新。`,
    );
  };

  const deleteEdge = async () => {
    if (!snapshot || !selectedEdge) return;
    const next = await mutate(
      "DELETE",
      `/plans/${encodeURIComponent(snapshot.plan_id)}/edges/${encodeURIComponent(selectedEdge.edge_id)}`,
      { expected_revision: snapshot.revision },
      `关系 ${selectedEdge.edge_id} 已删除。`,
    );
    if (next) setSelectedEdgeId(null);
  };

  const deriveRevision = async () => {
    if (!snapshot || !compareRecord || compareRecord.sequence === snapshot.revision) return;
    await mutate(
      "POST",
      `/plans/${encodeURIComponent(snapshot.plan_id)}/revisions/${compareRecord.sequence}/derive`,
      { expected_revision: snapshot.revision },
      `已从 revision ${compareRecord.sequence} 派生新的活动草案。`,
    );
  };

  const saveNodePosition = (node: PlanGraphNode) => {
    if (!snapshot || pending) return;
    const next = { ...layoutPositions, [node.id]: node.position };
    setLayoutPositions(next);
    try { window.localStorage.setItem(layoutKey(snapshot.plan_id), JSON.stringify(next)); } catch { /* view metadata remains in memory */ }
  };

  const archived = Boolean(snapshot?.is_archived);
  const editingDisabled = busy || pending || archived;

  return (
    <div className="workbench-page page-enter orchestration-page">
      <header className="page-titlebar orchestration-titlebar">
        <div>
          <span className="section-index">TASK ORCHESTRATION / {snapshot?.plan_id ?? "NEW PLAN"}</span>
          <h1><Network size={23} /> Interactive Task Planning</h1>
        </div>
        <div className="page-actions">
          <button className="button button-quiet" onClick={() => setShowPlanManager((value) => !value)}><ListTree size={15} /> 计划库 · {plans.length}</button>
          <button className="button button-quiet" onClick={() => { setShowCreate((value) => !value); setShowPlanManager(false); }}><CirclePlus size={15} /> 新计划</button>
          {snapshot && <button className="button button-quiet" disabled={busy || pending} onClick={toggleArchive}>{archived ? <RotateCcw size={15} /> : <Archive size={15} />} {archived ? "恢复" : "归档"}</button>}
          <button className="button button-primary" disabled={busy || !snapshot || !assessment?.ready_to_finalize || snapshot.status === "finalized" || archived} onClick={finalizePlan}><LockKeyhole size={15} /> 固化最终路径</button>
        </div>
      </header>

      {showPlanManager && (
        <section className="plan-manager panel-surface">
          <div className="plan-manager-head">
            <label><Search size={14} /><input value={planSearch} onChange={(event) => setPlanSearch(event.target.value)} placeholder="搜索计划 ID 或任务描述" autoFocus /></label>
            <label className="inline-check"><input type="checkbox" checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)} /> 显示归档</label>
            <button aria-label="关闭计划库" onClick={() => setShowPlanManager(false)}><X size={15} /></button>
          </div>
          <div className="plan-card-list">
            {filteredPlans.map((item) => (
              <button key={item.plan_id} className={item.plan_id === snapshot?.plan_id ? "active" : ""} onClick={() => void loadPlan(item.plan_id)}>
                <span><strong>{item.plan_id}</strong><i>{item.snapshot.is_archived ? "ARCHIVED" : item.snapshot.status.toUpperCase()}</i></span>
                <p>{item.snapshot.task_description}</p>
                <small>v{item.sequence} · {new Date(item.occurred_at).toLocaleString()} · {item.record_hash.slice(0, 10)}</small>
              </button>
            ))}
            {!filteredPlans.length && <p className="plan-manager-empty">没有匹配的计划。</p>}
          </div>
        </section>
      )}

      {showCreate && (
        <form className="plan-create-panel panel-surface" onSubmit={createPlan}>
          <div><span>NEW VERSIONED PLAN</span><strong>描述目标与边界</strong><small>系统只会创建待应用提案，不会启动执行。</small></div>
          <textarea value={taskDescription} onChange={(event) => setTaskDescription(event.target.value)} placeholder="描述希望完成的目标、范围、约束和验收方向……" rows={4} autoFocus />
          <button className="button button-primary" disabled={!session || busy || !taskDescription.trim()}><CirclePlus size={15} /> 创建并生成提案</button>
        </form>
      )}

      <div className={`orchestration-banner ${!session ? "offline" : archived ? "archived" : ""}`}>
        <ShieldCheck size={15} />
        <span><strong>{!session ? "API OFFLINE" : archived ? "ARCHIVED · READ ONLY" : "VERSIONED PLAN CONTROL"}</strong>{error ?? (snapshot ? `revision ${snapshot.revision} · ${pending ? "AGENT PROPOSAL PENDING" : snapshot.status.toUpperCase()} · execution disabled` : "输入任务后由系统创建首个计划 revision")}</span>
        {record && <code>{record.record_hash.slice(0, 12)}</code>}
      </div>

      {snapshot?.pending_proposal && proposalDiff && (
        <section className="proposal-review panel-surface">
          <div className="proposal-review-summary"><Bot size={18} /><span><strong>Agent 候选图等待用户决定</strong><p>{snapshot.pending_proposal.summary}</p><small>{snapshot.pending_proposal.agent_adapter}{snapshot.pending_proposal.agent_invocation_id ? ` · ${snapshot.pending_proposal.agent_invocation_id.slice(0, 20)}… · ${snapshot.pending_proposal.evidence_artifact_ids.length} IMMUTABLE ARTIFACTS` : " · OFFLINE FIXTURE"}</small></span></div>
          <div className="diff-metrics"><span className="added">+{proposalDiff.added} 新增</span><span className="changed">~{proposalDiff.changed} 修改</span><span className="removed">−{proposalDiff.removed} 删除</span></div>
          <div className="diff-change-list">
            {proposalDiff.changes.slice(0, 8).map((change) => <span key={`${change.entity}-${change.id}-${change.kind}`} className={change.kind}><b>{change.kind === "added" ? "+" : change.kind === "removed" ? "−" : "~"}</b>{change.entity} · {change.label}</span>)}
            {proposalDiff.changes.length > 8 && <small>另有 {proposalDiff.changes.length - 8} 项变化</small>}
          </div>
          <div className="proposal-review-actions"><button className="button button-quiet" disabled={busy} onClick={rejectProposal}><X size={14} /> 拒绝，保留当前图</button><button className="button button-primary" disabled={busy} onClick={applyProposal}><Check size={14} /> 应用完整候选图</button></div>
        </section>
      )}

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
                  <span>{message.role === "agent" ? <Bot size={14} /> : message.role === "system" ? <ShieldCheck size={14} /> : <UserRound size={14} />}{message.role === "agent" ? message.agent_adapter : message.role === "system" ? "JobSlayer" : session?.principal.display_name ?? "User"}</span>
                  <p>{message.content}</p>
                  <time>{new Date(message.created_at).toLocaleTimeString()}</time>
                </article>
              ))}
            </div>
            <form className="discussion-compose" onSubmit={sendDiscussion}>
              <textarea value={discussion} onChange={(event) => setDiscussion(event.target.value)} disabled={archived} placeholder={selected ? `围绕“${selected.title}”继续讨论；可说明修改、删除、支线、子任务或依赖…` : "继续细化任务…"} rows={4} />
              <button className="button button-primary" disabled={busy || archived || !discussion.trim()}><Send size={14} /> 发送并生成提案</button>
            </form>
          </aside>

          <section className="orchestration-canvas panel-surface">
            <div className="canvas-tabs">
              <button className="active">Plan topology</button>
              <span>{effectiveNodes.length} nodes · {effectiveEdges.length} edges · {pending ? "proposal view" : "applied view"}</span>
              <label>新连线<select value={newEdgeRelation} onChange={(event) => setNewEdgeRelation(event.target.value as TaskPlanEdgeRelation)} disabled={editingDisabled}><option value="sequence">顺序</option><option value="dependency">依赖</option><option value="branch">支线</option><option value="subtask">子任务</option></select></label>
            </div>
            <div className="flow-canvas">
              <ReactFlow
                key={`${snapshot.plan_id}-${snapshot.revision}-${snapshot.pending_proposal?.proposal_id ?? "applied"}`}
                defaultNodes={graph.nodes}
                defaultEdges={graph.edges}
                nodeTypes={nodeTypes}
                onNodeClick={(_, node) => { setSelectedId(node.id); setSelectedEdgeId(null); }}
                onEdgeClick={(_, edge) => { setSelectedEdgeId(edge.id); setSelectedId(null); }}
                onPaneClick={() => { setSelectedId(null); setSelectedEdgeId(null); }}
                onConnect={(connection) => void createEdge(connection)}
                onNodeDragStop={(_, node) => saveNodePosition(node as PlanGraphNode)}
                nodesDraggable={!pending}
                nodesConnectable={!editingDisabled}
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
            {snapshot.pending_proposal && <div className="proposal-strip"><Bot size={14} /><span><strong>候选图视图</strong>虚线节点和边尚未进入已应用计划；请在上方查看差异后决定。</span></div>}
          </section>

          <aside className="orchestration-inspector panel-surface">
            <div className="quality-summary">
              <span className="panel-label">PLAN READINESS</span>
              <div className={assessment?.ready_to_finalize ? "ready" : "blocked"}><ShieldCheck size={16} /><span><strong>{assessment?.ready_to_finalize ? "可以固化" : `${blockerCount} 个阻断项`}</strong><small>{warningCount} warnings · v{assessment?.revision ?? snapshot.revision}</small></span></div>
              <div className="quality-issues">
                {assessment?.issues.slice(0, 6).map((issue) => (
                  <button key={`${issue.code}-${issue.node_id ?? "plan"}`} className={issue.severity} onClick={() => { if (issue.node_id) { setSelectedId(issue.node_id); setSelectedEdgeId(null); } }}>
                    {issue.severity === "blocker" ? <AlertTriangle size={12} /> : <Info size={12} />}<span>{issue.message}</span>
                  </button>
                ))}
                {assessment && assessment.issues.length > 6 && <small>另有 {assessment.issues.length - 6} 项提示</small>}
              </div>
            </div>

            <div className="inspector-divider" />
            <div className="panel-label">{selectedEdge ? "EDGE CRUD" : "NODE CRUD"} / REVISION</div>
            {selectedEdge ? (
              <div className="edge-editor node-editor">
                <div className="selected-node-title"><span><Link2 size={15} /></span><div><strong>{selectedEdge.source_node_id} → {selectedEdge.target_node_id}</strong><small>{selectedEdge.edge_id}</small></div></div>
                <label>关系类型<select value={editEdgeRelation} onChange={(event) => setEditEdgeRelation(event.target.value as TaskPlanEdgeRelation)} disabled={editingDisabled}><option value="sequence">顺序</option><option value="dependency">依赖</option><option value="branch">支线</option><option value="subtask">子任务</option></select></label>
                <label>关系说明<input value={editEdgeLabel} onChange={(event) => setEditEdgeLabel(event.target.value)} disabled={editingDisabled} placeholder="可选语义标签" /></label>
                <div className="node-editor-actions"><button className="button button-quiet" onClick={saveEdge} disabled={editingDisabled}><Save size={14} /> 保存</button><button className="button button-danger-quiet" onClick={deleteEdge} disabled={editingDisabled}><Trash2 size={14} /> 删除</button></div>
              </div>
            ) : selected ? (
              <div className="node-editor">
                <div className="selected-node-title"><span><ListTree size={15} /></span><div><strong>{selected.title}</strong><small>{selected.node_id}</small></div></div>
                <label>标题<input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} disabled={editingDisabled} /></label>
                <label>详细描述<textarea value={editDescription} onChange={(event) => setEditDescription(event.target.value)} rows={4} disabled={editingDisabled} /></label>
                <label>节点类型<select value={editKind} onChange={(event) => setEditKind(event.target.value as TaskPlanNodeKind)} disabled={editingDisabled}><option value="task">Task</option><option value="milestone">Milestone</option><option value="validation">Validation</option><option value="human_gate">Human gate</option></select></label>
                <label>执行提示<input value={editExecutor} onChange={(event) => setEditExecutor(event.target.value)} disabled={editingDisabled} placeholder="provider-neutral hint" /></label>
                <label>验收标准（每行一项）<textarea value={editAcceptance} onChange={(event) => setEditAcceptance(event.target.value)} rows={3} disabled={editingDisabled} /></label>
                <label>交付物（每行一项）<textarea value={editDeliverables} onChange={(event) => setEditDeliverables(event.target.value)} rows={3} disabled={editingDisabled} /></label>
                <label>约束（每行一项）<textarea value={editConstraints} onChange={(event) => setEditConstraints(event.target.value)} rows={3} disabled={editingDisabled} /></label>
                <label>风险（每行一项）<textarea value={editRisks} onChange={(event) => setEditRisks(event.target.value)} rows={3} disabled={editingDisabled} /></label>
                <label>验证要求（每行一项）<textarea value={editVerification} onChange={(event) => setEditVerification(event.target.value)} rows={3} disabled={editingDisabled} /></label>
                <label className="inline-check"><input type="checkbox" checked={editHumanDecision} onChange={(event) => setEditHumanDecision(event.target.checked)} disabled={editingDisabled} /> 需要人工决策</label>
                <div className="node-editor-actions"><button className="button button-quiet" onClick={saveNode} disabled={editingDisabled || !editTitle.trim()}><Save size={14} /> 保存</button><button className="button button-danger-quiet" onClick={deleteNode} disabled={editingDisabled}><Trash2 size={14} /> 删除</button></div>
              </div>
            ) : <p className="empty-inspector">选择节点或关系以查看和修改。</p>}

            <div className="split-editor">
              <span>CREATE / SPLIT</span>
              <input value={childTitle} onChange={(event) => setChildTitle(event.target.value)} placeholder="新节点标题" disabled={editingDisabled} />
              <div><button onClick={() => void addNode()} disabled={editingDisabled || !childTitle.trim()}><CirclePlus size={13} /> 独立节点</button><button onClick={() => void addNode("branch")} disabled={editingDisabled || !selected || !childTitle.trim()}><GitBranch size={13} /> 支线</button><button onClick={() => void addNode("subtask")} disabled={editingDisabled || !selected || !childTitle.trim()}><Split size={13} /> 子任务</button></div>
            </div>

            <div className="revision-compare">
              <span>REVISION COMPARE</span>
              {compareRecord && revisionDiff ? <><div className="revision-compare-head"><GitCompare size={14} /><strong>v{compareRecord.sequence} → v{snapshot.revision}</strong></div><div className="diff-metrics"><span className="added">+{revisionDiff.added}</span><span className="changed">~{revisionDiff.changed}</span><span className="removed">−{revisionDiff.removed}</span></div><div className="diff-change-list compact">{revisionDiff.changes.slice(0, 5).map((change) => <span key={`${change.entity}-${change.id}-${change.kind}`} className={change.kind}>{change.kind === "added" ? "+" : change.kind === "removed" ? "−" : "~"} {change.label}</span>)}</div><button className="button button-quiet" onClick={deriveRevision} disabled={editingDisabled || compareRecord.sequence === snapshot.revision}><RotateCcw size={13} /> 从 v{compareRecord.sequence} 派生新草案</button></> : <small>选择一个历史版本进行比较。</small>}
            </div>

            <div className="revision-list">
              <span>APPEND-ONLY HISTORY</span>
              {[...history].reverse().slice(0, 12).map((item) => <button key={item.record_id} className={compareRevision === item.sequence ? "active" : ""} onClick={() => setCompareRevision(item.sequence)}><b>v{item.sequence}</b><p>{item.operation}<small>{item.record_hash.slice(0, 12)}</small></p></button>)}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
