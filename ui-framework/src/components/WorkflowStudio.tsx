import { useCallback, useMemo, useState } from "react";
import Editor from "@monaco-editor/react";
import {
  addEdge,
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { Bot, CheckCircle2, CirclePlus, GitBranch, ShieldCheck, UserCheck, Wrench } from "lucide-react";

type WorkflowNodeData = {
  label: string;
  type: "agent_task" | "validation" | "human_gate" | "report";
  owner: string;
  status: "ready" | "running" | "waiting";
  validation: string;
};

type WorkflowGraphNode = Node<WorkflowNodeData, "workflow">;

const initialNodes: WorkflowGraphNode[] = [
  { id: "planner", type: "workflow", position: { x: 70, y: 130 }, data: { label: "Plan change", type: "agent_task", owner: "planner", status: "ready", validation: "schema" } },
  { id: "developer", type: "workflow", position: { x: 330, y: 130 }, data: { label: "Implement", type: "agent_task", owner: "coder", status: "running", validation: "patch policy" } },
  { id: "tests", type: "workflow", position: { x: 610, y: 42 }, data: { label: "Verify", type: "validation", owner: "deterministic", status: "waiting", validation: "3 commands" } },
  { id: "review", type: "workflow", position: { x: 610, y: 230 }, data: { label: "Human review", type: "human_gate", owner: "reviewer", status: "waiting", validation: "approval actor" } },
  { id: "report", type: "workflow", position: { x: 885, y: 130 }, data: { label: "Publish report", type: "report", owner: "artifact service", status: "waiting", validation: "hash + metadata" } },
];

const initialEdges: Edge[] = [
  { id: "e1", source: "planner", target: "developer", animated: true, markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: "#8b90a0" } },
  { id: "e2", source: "developer", target: "tests", markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: "#8b90a0" } },
  { id: "e3", source: "developer", target: "review", markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: "#8b90a0" } },
  { id: "e4", source: "tests", target: "report", markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: "#8b90a0" } },
  { id: "e5", source: "review", target: "report", markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: "#8b90a0" } },
];

function WorkNode({ data, selected }: NodeProps<WorkflowGraphNode>) {
  const Icon = data.type === "agent_task" ? Bot : data.type === "validation" ? ShieldCheck : data.type === "human_gate" ? UserCheck : GitBranch;
  return (
    <div className={`flow-node ${data.status} ${selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="flow-node-top"><span className="flow-node-icon"><Icon size={15} /></span><span>{data.type}</span><span className="flow-state" /></div>
      <strong>{data.label}</strong>
      <small>{data.owner}</small>
      <div className="flow-validation"><CheckCircle2 size={12} /> {data.validation}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const workflowIr = `workflow:
  id: ui-framework-reference
  version: 12

nodes:
  - id: developer
    type: agent_task
    agent:
      capability: coding
    input:
      context:
        - planning.output
        - repository.current_state
    policy:
      timeout: 30m
      retries: 2
    validation:
      - compile
      - unit_test
    outputs:
      - patch
      - implementation_report

# React Flow positions are presentation metadata and are
# deliberately excluded from this canonical mock IR.
`;

interface WorkflowStudioProps {
  onNotice: (message: string) => void;
}

export function WorkflowStudio({ onNotice }: WorkflowStudioProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState<WorkflowGraphNode>(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [selectedId, setSelectedId] = useState("developer");
  const [tab, setTab] = useState<"graph" | "ir">("graph");
  const nodeTypes = useMemo(() => ({ workflow: WorkNode }), []);
  const selected = nodes.find((node) => node.id === selectedId) ?? nodes[0];
  const onConnect = useCallback((connection: Connection) => setEdges((current) => addEdge({ ...connection, markerEnd: { type: MarkerType.ArrowClosed } }, current)), [setEdges]);

  const addHumanGate = () => {
    const nextId = `gate-${nodes.length + 1}`;
    setNodes((current) => [
      ...current,
      { id: nextId, type: "workflow", position: { x: 430, y: 340 }, data: { label: "New approval gate", type: "human_gate", owner: "authorized actor", status: "waiting", validation: "verification required" } },
    ]);
    setSelectedId(nextId);
    onNotice("已在本地画布添加人工门节点；没有生成工作流命令。 ");
  };

  return (
    <div className="workbench-page page-enter">
      <header className="page-titlebar">
        <div><span className="section-index">WORKFLOW / UI-FRAMEWORK-REFERENCE / V12</span><h1>Workflow Studio</h1></div>
        <div className="page-actions">
          <button className="button button-quiet" onClick={addHumanGate}><CirclePlus size={15} /> 添加人工门</button>
          <button className="button button-primary" onClick={() => onNotice("演示模式：运行命令未提交。真实实现必须调用 Control Plane command API。 ")}><GitBranch size={15} /> 检查运行意图</button>
        </div>
      </header>

      <div className="prototype-banner"><ShieldCheck size={15} /><span><strong>交互原型</strong> 画布编辑只改变浏览器内存；Canonical Workflow IR 与 Kernel 均未连接。</span></div>

      <div className="studio-grid">
        <aside className="node-library panel-surface">
          <div className="panel-label">NODE LIBRARY</div>
          <div className="library-group"><span>EXECUTION</span><button><Bot size={15} /> Agent task <small>拖到画布</small></button><button><Wrench size={15} /> Tool call <small>受控工具</small></button></div>
          <div className="library-group"><span>GOVERNANCE</span><button><ShieldCheck size={15} /> Validation <small>确定性门禁</small></button><button><UserCheck size={15} /> Human gate <small>显式审批</small></button></div>
          <div className="library-note">组件库演示视觉与分类；拖放建模将在 Workflow IR adapter 确定后实现。</div>
        </aside>

        <section className="studio-canvas panel-surface">
          <div className="canvas-tabs" role="tablist" aria-label="工作流视图">
            <button role="tab" aria-selected={tab === "graph"} className={tab === "graph" ? "active" : ""} onClick={() => setTab("graph")}>Graph</button>
            <button role="tab" aria-selected={tab === "ir"} className={tab === "ir" ? "active" : ""} onClick={() => setTab("ir")}>Canonical IR</button>
            <span>{nodes.length} nodes · {edges.length} edges</span>
          </div>
          {tab === "graph" ? (
            <div className="flow-canvas">
              <ReactFlow
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                onNodeClick={(_, node) => setSelectedId(node.id)}
                fitView
                minZoom={0.55}
                maxZoom={1.6}
                aria-label="示范工作流图"
              >
                <Background variant={BackgroundVariant.Dots} color="#30343d" gap={22} size={1} />
                <MiniMap nodeColor={(node) => node.id === selectedId ? "#b9ff66" : "#555b66"} maskColor="rgba(10,12,15,.76)" />
                <Controls showInteractive={false} />
              </ReactFlow>
            </div>
          ) : (
            <Editor height="100%" defaultLanguage="yaml" value={workflowIr} theme="vs-dark" options={{ readOnly: true, minimap: { enabled: false }, fontSize: 13, lineHeight: 22, padding: { top: 18 }, scrollBeyondLastLine: false }} />
          )}
        </section>

        <aside className="inspector panel-surface">
          <div className="panel-label">CONTEXT INSPECTOR</div>
          <div className="inspector-title"><span className="flow-node-icon"><Bot size={16} /></span><div><strong>{selected.data.label}</strong><small>{selected.id}</small></div></div>
          <label>Node type<input value={selected.data.type} readOnly /></label>
          <label>Capability<input value={selected.data.owner} readOnly /></label>
          <div className="field-row"><label>Timeout<input value="30m" readOnly /></label><label>Retries<input value="2" readOnly /></label></div>
          <div className="inspector-block"><span>REQUIRED VALIDATION</span><div><CheckCircle2 size={14} /> compile</div><div><CheckCircle2 size={14} /> unit_test</div></div>
          <div className="inspector-block"><span>OUTPUTS</span><code>patch</code><code>implementation_report</code></div>
          <div className="authority-callout"><ShieldCheck size={16} /><p><strong>Authority note</strong>重试数、验证规则和权限只在 canonical contract 中生效；Inspector 不直接控制执行器。</p></div>
        </aside>
      </div>
    </div>
  );
}
