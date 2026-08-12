export type ViewId = "overview" | "workflow" | "run" | "artifact" | "observability";

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
