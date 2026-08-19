import type { TaskPlanEdge, TaskPlanNode } from "./types";

export type PlanChangeKind = "added" | "removed" | "changed";

export interface PlanChange {
  kind: PlanChangeKind;
  entity: "node" | "edge";
  id: string;
  label: string;
}

export interface PlanDiff {
  changes: PlanChange[];
  added: number;
  removed: number;
  changed: number;
}

function stable(value: TaskPlanNode | TaskPlanEdge): string {
  return JSON.stringify(value);
}

export function diffPlanGraphs(
  beforeNodes: TaskPlanNode[],
  beforeEdges: TaskPlanEdge[],
  afterNodes: TaskPlanNode[],
  afterEdges: TaskPlanEdge[],
): PlanDiff {
  const changes: PlanChange[] = [];
  const beforeNodeMap = new Map(beforeNodes.map((node) => [node.node_id, node]));
  const afterNodeMap = new Map(afterNodes.map((node) => [node.node_id, node]));
  const beforeEdgeMap = new Map(beforeEdges.map((edge) => [edge.edge_id, edge]));
  const afterEdgeMap = new Map(afterEdges.map((edge) => [edge.edge_id, edge]));

  for (const node of afterNodes) {
    const previous = beforeNodeMap.get(node.node_id);
    if (!previous) changes.push({ kind: "added", entity: "node", id: node.node_id, label: node.title });
    else if (stable(previous) !== stable(node)) changes.push({ kind: "changed", entity: "node", id: node.node_id, label: node.title });
  }
  for (const node of beforeNodes) {
    if (!afterNodeMap.has(node.node_id)) changes.push({ kind: "removed", entity: "node", id: node.node_id, label: node.title });
  }
  for (const edge of afterEdges) {
    const previous = beforeEdgeMap.get(edge.edge_id);
    const label = `${edge.source_node_id} → ${edge.target_node_id}`;
    if (!previous) changes.push({ kind: "added", entity: "edge", id: edge.edge_id, label });
    else if (stable(previous) !== stable(edge)) changes.push({ kind: "changed", entity: "edge", id: edge.edge_id, label });
  }
  for (const edge of beforeEdges) {
    if (!afterEdgeMap.has(edge.edge_id)) {
      changes.push({ kind: "removed", entity: "edge", id: edge.edge_id, label: `${edge.source_node_id} → ${edge.target_node_id}` });
    }
  }

  return {
    changes,
    added: changes.filter((change) => change.kind === "added").length,
    removed: changes.filter((change) => change.kind === "removed").length,
    changed: changes.filter((change) => change.kind === "changed").length,
  };
}

export function splitLines(value: string): string[] {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

export function joinLines(value: string[]): string {
  return value.join("\n");
}
