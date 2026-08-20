export type ViewId = "task-manager" | "overview" | "orchestration" | "workflow" | "run" | "artifact" | "observability";

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
  execution_target_id: string | null;
  execution_target_source_sha256: string | null;
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

export interface TaskManagerExecutionTarget {
  schema_version: "1.0";
  target_id: string;
  display_name: string;
  testbed_id: string;
  task_id: string;
  task_title: string;
  repository: string;
  baseline_commit: string;
  checkout_path: string;
  allowed_paths: string[];
  forbidden_paths: string[];
  validation_profile_id: string;
  validation_commands: string[][];
  executor_adapter: string;
  model_profile: string;
  executor_model: string | null;
  executor_reasoning_effort: "none" | "low" | "medium" | "high" | "xhigh" | "max" | null;
  timeout_seconds: number;
  maximum_input_tokens: number;
  maximum_output_tokens: number;
  maximum_context_bytes: number;
  maximum_cost_usd: number;
  local_baseline_ready: boolean;
  source_bundle_sha256: string;
}

export interface TaskManagerExecutionTargetAssessment {
  schema_version: "1.0";
  plan_id: string;
  revision: number;
  target_id: string;
  ready: boolean;
  issues: TaskPlanIssue[];
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

export interface PlanningArtifactDescriptor {
  schema_version: "1.0";
  artifact_id: string;
  plan_id: string;
  invocation_id: string | null;
  artifact_type: string;
  sha256: string;
  size_bytes: number;
  producer: string;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface PlanningArtifactPreview {
  schema_version: "1.0";
  artifact: PlanningArtifactDescriptor;
  content: string;
  encoding: "utf-8";
  preview_size_bytes: number;
  truncated: boolean;
  content_verified: boolean;
}

export type ManagedTaskStage =
  | "proposal_pending"
  | "planning"
  | "ready"
  | "running"
  | "needs_attention"
  | "verifying"
  | "completed"
  | "failed"
  | "cancelled"
  | "archived";

export type ManagedNodeState =
  | "proposed"
  | "planned"
  | "ready"
  | "running"
  | "waiting"
  | "blocked"
  | "verifying"
  | "completed"
  | "failed"
  | "cancelled";

export interface ManagedTaskSummary {
  schema_version: "1.0";
  task_id: string;
  title: string;
  task_description: string;
  revision: number;
  stage: ManagedTaskStage;
  pending_proposal: boolean;
  node_count: number;
  backlog_count: number;
  blocker_count: number;
  is_archived: boolean;
  updated_at: string;
  record_hash: string;
}

export interface ManagedNodeView {
  schema_version: "1.0";
  node: TaskPlanNode;
  state: ManagedNodeState;
  dependency_node_ids: string[];
  issue_codes: string[];
}

export interface TaskManagerBacklogItem {
  schema_version: "1.0";
  node_id: string;
  title: string;
  state: ManagedNodeState;
  reason: string;
  dependency_node_ids: string[];
}

export interface TaskManagerLogEntry {
  schema_version: "1.0";
  log_id: string;
  category: "planning" | "conversation" | "decision" | "execution" | "feedback" | "verification";
  event_type: string;
  summary: string;
  actor_type: "human" | "agent" | "policy" | "system";
  actor_id: string;
  occurred_at: string;
  revision: number;
  record_hash: string;
  node_id: string | null;
}

export type WorkflowTaskState =
  | "draft"
  | "planned"
  | "plan_review"
  | "implementing"
  | "verifying"
  | "repairing"
  | "reviewing"
  | "merge_review"
  | "integrating"
  | "blocked"
  | "failed"
  | "completed"
  | "cancelled"
  | "gate_approved"
  | "deliverable_accepted";

export interface ManagedExecutionReference {
  schema_version: "1.0";
  provider_start_key: string;
  adapter_id: string;
  provider_run_id: string;
  started_at: string;
  evidence_artifact_ids: string[];
}

export interface ManagedExecutionObservation {
  schema_version: "1.0";
  provider_run_id: string;
  status: "running" | "succeeded" | "failed" | "cancelled";
  cursor: string;
  summary: string;
  observed_at: string;
  evidence_artifact_ids: string[];
}

export interface ManagedVerificationEvidence {
  schema_version: "1.0";
  provider_run_id: string;
  source_commit: string;
  source_patch_sha256: string | null;
  workspace: {
    workspace_id: string;
    task_id: string;
    head_commit: string;
    branch_name: string;
    changed_paths: string[];
    working_tree_clean: boolean;
  };
  collected_at: string;
  evidence_artifact_ids: string[];
}

export interface TaskManagerVerificationReport {
  schema_version: "1.0";
  report_id: string;
  task_id: string;
  source_commit: string;
  source_patch_sha256: string | null;
  checks: Array<{
    check_id: string;
    status: "passed" | "failed" | "error" | "skipped";
    required: boolean;
    summary: string;
    evidence_hash: string;
  }>;
  required_checks_passed: boolean;
  regressions_detected: boolean;
  unresolved_risks: string[];
  created_at: string;
}

export interface TaskManagerSourceReview {
  schema_version: "1.0";
  review_id: string;
  task_id: string;
  reviewer_actor_type: "human" | "agent";
  reviewer_id: string;
  patch_sha256: string;
  status: "accepted" | "changes_requested";
  summary: string;
  findings: string[];
  evidence_ids: string[];
  created_at: string;
}

export interface TaskManagerSourceIntegrationResult {
  schema_version: "1.0";
  integration_id: string;
  task_id: string;
  workspace_id: string;
  repository_root: string;
  source_ref: string;
  target_ref: string;
  base_commit: string;
  commit: string;
  target_previous_commit: string;
  target_commit: string;
  source_patch_sha256: string;
  changed_paths: string[];
  approved_by: string;
  integrated_at: string;
}

export interface TaskManagerNodeExecution {
  schema_version: "1.0";
  node: TaskPlanNode;
  workflow_task_id: string;
  dependency_node_ids: string[];
  workflow_state: WorkflowTaskState;
  transition_history: Array<{
    schema_version: "1.0";
    task_id: string;
    sequence: number;
    from_state: WorkflowTaskState;
    to_state: WorkflowTaskState;
    actor_type: "human" | "agent" | "policy" | "system";
    actor_id: string;
    reason: string;
    evidence_ids: string[];
    occurred_at: string;
    previous_hash: string | null;
    record_hash: string;
  }>;
  provider_start_key: string | null;
  provider_reference: ManagedExecutionReference | null;
  latest_observation: ManagedExecutionObservation | null;
  verification_evidence: ManagedVerificationEvidence | null;
  verification_report: TaskManagerVerificationReport | null;
  verification_artifact_id: string | null;
  review_artifact_id: string | null;
  source_review: TaskManagerSourceReview | null;
  source_review_artifact_id: string | null;
  source_approval_artifact_id: string | null;
  source_approved_by: string | null;
  integration_key: string | null;
  integration_result: TaskManagerSourceIntegrationResult | null;
  integration_artifact_id: string | null;
}

export interface TaskManagerRunSnapshot {
  schema_version: "1.0";
  run_id: string;
  revision: number;
  plan_id: string;
  plan_revision: number;
  plan_record_hash: string;
  execution_binding: { target_id: string; source_bundle_sha256: string };
  stage: "ready" | "running" | "needs_attention" | "verifying" | "completed" | "cancelled";
  nodes: TaskManagerNodeExecution[];
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface ManagedTaskDetail {
  schema_version: "1.0";
  task: ManagedTaskSummary;
  plan: TaskPlanSnapshot;
  assessment: TaskPlanAssessment;
  nodes: ManagedNodeView[];
  backlog: TaskManagerBacklogItem[];
  log: TaskManagerLogEntry[];
  execution_targets: TaskManagerExecutionTarget[];
  execution_target: TaskManagerExecutionTarget | null;
  execution_target_assessment: TaskManagerExecutionTargetAssessment | null;
  execution_run: TaskManagerRunSnapshot | null;
  run_assembly_available: boolean;
  execution_available: boolean;
  execution_blockers: string[];
}

export interface TaskManagerSession {
  schema_version: "1.0";
  principal: { subject_id: string; display_name: string; roles: string[] };
  agent_adapter: string;
  submission_token: string;
  capabilities: {
    task_planning: boolean;
    agent_discussion: boolean;
    dag_preview: boolean;
    proposal_decisions: boolean;
    finalization: boolean;
    multi_task_history: boolean;
    planning_artifact_viewer: boolean;
    run_assembly: boolean;
    execution_target_binding: boolean;
    task_execution: boolean;
    execution_feedback: boolean;
    node_verification: boolean;
    node_validation: boolean;
    completion_approval: boolean;
    node_review: boolean;
    source_review: boolean;
    source_checkpoint_approval: boolean;
    source_checkpoint_integration: boolean;
  };
}
