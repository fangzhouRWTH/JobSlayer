export type ViewId = "overview" | "orchestration" | "workflow" | "run" | "artifact" | "observability";

export type WorkbenchStatus = "success" | "running" | "waiting" | "failed" | "review";

export interface RunTask {
  id: string;
  title: string;
  owner: string;
  status: WorkbenchStatus;
  duration: string;
  detail: string;
  validation: string;
}

export interface ExecutionEvent {
  id: string;
  type: string;
  time: string;
  task: string;
  summary: string;
  level: "info" | "success" | "warning" | "error";
}

export interface ArtifactItem {
  id: string;
  name: string;
  type: "markdown" | "diff" | "json";
  producer: string;
  relatedTask: string;
  version: string;
  size: string;
  sha256: string;
  validation: "passed" | "pending";
}

export type TaskPlanStatus = "draft" | "finalized";
export type TaskPlanNodeKind = "task" | "milestone" | "validation" | "human_gate";
export type TaskPlanEdgeRelation = "sequence" | "dependency" | "branch" | "subtask";

export interface TaskPlanNode {
  schema_version: "1.0";
  node_id: string;
  title: string;
  description: string;
  kind: TaskPlanNodeKind;
  executor_hint: string | null;
  acceptance_criteria: string[];
  deliverables: string[];
  constraints: string[];
  risks: string[];
  verification_requirements: string[];
  requires_human_decision: boolean;
  attributes: Record<string, string>;
}

export interface TaskPlanEdge {
  schema_version: "1.0";
  edge_id: string;
  source_node_id: string;
  target_node_id: string;
  relation: TaskPlanEdgeRelation;
  label: string | null;
}

export interface TaskPlanMessage {
  schema_version: "1.0";
  message_id: string;
  role: "user" | "agent" | "system";
  content: string;
  created_at: string;
  agent_adapter: string | null;
}

export interface TaskPlanProposal {
  schema_version: "1.0";
  proposal_id: string;
  based_on_revision: number;
  summary: string;
  agent_adapter: string;
  nodes: TaskPlanNode[];
  edges: TaskPlanEdge[];
  agent_invocation_id: string | null;
  evidence_artifact_ids: string[];
  created_at: string;
}

export interface TaskPlanSnapshot {
  schema_version: "1.0";
  plan_id: string;
  revision: number;
  task_description: string;
  status: TaskPlanStatus;
  nodes: TaskPlanNode[];
  edges: TaskPlanEdge[];
  conversation: TaskPlanMessage[];
  pending_proposal: TaskPlanProposal | null;
  latest_finalized_revision: number | null;
  finalized_by: string | null;
  finalized_at: string | null;
  is_archived: boolean;
  archived_by: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskPlanRevisionRecord {
  schema_version: "1.0";
  record_id: string;
  plan_id: string;
  sequence: number;
  snapshot: TaskPlanSnapshot;
  actor_type: "human" | "agent" | "policy" | "system";
  actor_id: string;
  operation: string;
  occurred_at: string;
  previous_hash: string | null;
  record_hash: string;
}

export type TaskPlanIssueSeverity = "blocker" | "warning" | "info";

export interface TaskPlanIssue {
  schema_version: "1.0";
  code: string;
  severity: TaskPlanIssueSeverity;
  message: string;
  node_id: string | null;
}

export interface TaskPlanAssessment {
  schema_version: "1.0";
  plan_id: string;
  revision: number;
  ready_to_finalize: boolean;
  issues: TaskPlanIssue[];
}
