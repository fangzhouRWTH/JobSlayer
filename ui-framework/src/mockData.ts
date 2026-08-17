import type { ArtifactItem, ExecutionEvent, RunTask } from "./types";

export const runTasks: RunTask[] = [
  {
    id: "task-plan",
    title: "拆解交互框架目标",
    owner: "planner-01",
    status: "success",
    duration: "08.3s",
    detail: "生成 5 个有依赖关系的工作包",
    validation: "Schema valid",
  },
  {
    id: "task-implement",
    title: "实现 Workbench 原型",
    owner: "coder-02",
    status: "running",
    duration: "02m 12s",
    detail: "读取 24 个文件 · 修改 7 个文件",
    validation: "Typecheck running",
  },
  {
    id: "task-verify",
    title: "确定性验证",
    owner: "validator-local",
    status: "waiting",
    duration: "—",
    detail: "等待实现任务生成补丁制品",
    validation: "3 checks queued",
  },
  {
    id: "task-review",
    title: "人工合并审查",
    owner: "authorized-reviewer",
    status: "review",
    duration: "—",
    detail: "只在验证报告通过后开放",
    validation: "Approval required",
  },
];

export const executionEvents: ExecutionEvent[] = [
  {
    id: "evt_10201",
    type: "run.started",
    time: "14:02:11.083",
    task: "run_ui_028",
    summary: "Workflow version wf_ui_v12 admitted by Control Plane",
    level: "info",
  },
  {
    id: "evt_10202",
    type: "task.completed",
    time: "14:02:19.421",
    task: "task-plan",
    summary: "Planner output registered as artifact plan_104",
    level: "success",
  },
  {
    id: "evt_10203",
    type: "worker.assigned",
    time: "14:02:20.008",
    task: "task-implement",
    summary: "worker windows-dev-01 · lease 20m · network denied",
    level: "info",
  },
  {
    id: "evt_10204",
    type: "tool.completed",
    time: "14:03:46.772",
    task: "task-implement",
    summary: "Repository read · 24 files · raw log retained",
    level: "success",
  },
  {
    id: "evt_10205",
    type: "validation.started",
    time: "14:04:23.114",
    task: "task-implement",
    summary: "TypeScript contract check started",
    level: "warning",
  },
];

export const artifacts: ArtifactItem[] = [
  {
    id: "artifact_report_104",
    name: "implementation-report.md",
    type: "markdown",
    producer: "coder-02",
    relatedTask: "task-implement",
    version: "v3",
    size: "4.8 KB",
    sha256: "4f0b1ca8d43f71077f882675d8a33b8d0b6bdb6c8a7a76d716a6f5a0f6b8fcd1",
    validation: "passed",
  },
  {
    id: "artifact_patch_112",
    name: "workbench.patch",
    type: "diff",
    producer: "coder-02",
    relatedTask: "task-implement",
    version: "v3",
    size: "18.2 KB",
    sha256: "a5bfbc7f9389f7e8bcb2668331cafe31772870f15435e58aece7433f2e4d9aba",
    validation: "passed",
  },
  {
    id: "artifact_events_088",
    name: "normalized-events.json",
    type: "json",
    producer: "control-plane",
    relatedTask: "run_ui_028",
    version: "v1",
    size: "12.7 KB",
    sha256: "7d3c99922a820b01e1fe32aa4e8193b768196f9339f41c8fcfe0e18213d6a814",
    validation: "pending",
  },
];

export const reportMarkdown = `# Implementation report

The Stage 0 workbench prototype keeps presentation state outside the JobSlayer control plane.

## Verification summary

- TypeScript contracts compile without errors.
- Workflow graph data is adapted from a canonical mock IR.
- Review actions remain local demonstrations and never mutate task state.

## Provenance

Produced by \`coder-02\` from workflow version \`wf_ui_v12\`. The patch, normalized events, and validation output remain independently addressable artifacts.
`;

export const originalCode = `export function approve(task: Task) {
  task.state = "completed";
  return task;
}`;

export const modifiedCode = `export function requestApproval(
  command: ApprovalCommand,
  kernel: WorkflowKernel,
) {
  return kernel.transition(command);
}`;
