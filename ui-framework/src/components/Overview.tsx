import {
  Activity,
  ArrowRight,
  Boxes,
  Braces,
  CheckCircle2,
  Database,
  FileSearch,
  GitBranch,
  Network,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import type { ViewId } from "../types";

interface OverviewProps {
  onNavigate: (view: ViewId) => void;
}

const demos: Array<{
  id: ViewId;
  number: string;
  title: string;
  summary: string;
  eyebrow: string;
  icon: typeof Network;
}> = [
  {
    id: "workflow",
    number: "01",
    title: "Workflow Studio",
    summary: "编辑图形、选择节点并检查与图编辑器解耦的 Workflow IR。",
    eyebrow: "AUTHOR",
    icon: Network,
  },
  {
    id: "run",
    number: "02",
    title: "Run Inspector",
    summary: "从任务、调用、工具和验证层级理解执行，不在纯日志中迷失。",
    eyebrow: "OBSERVE",
    icon: Activity,
  },
  {
    id: "artifact",
    number: "03",
    title: "Artifact Review",
    summary: "把报告、Diff、元数据、验证和人工决定放进同一审查上下文。",
    eyebrow: "REVIEW",
    icon: FileSearch,
  },
  {
    id: "observability",
    number: "04",
    title: "Observability",
    summary: "以 Trace、事件和治理指标解释系统，而非用 Agent 自信度代替证据。",
    eyebrow: "LEARN",
    icon: GitBranch,
  },
];

const stack = [
  ["React + TypeScript + Vite", "工作台外壳", "ADOPTED"],
  ["React Flow", "工作流图交互", "ADOPTED"],
  ["Monaco", "IR、代码与 Diff", "ADOPTED"],
  ["xterm.js", "只读终端示例", "ADOPTED"],
  ["Apache ECharts", "指标与 Trace", "ADOPTED"],
  ["PDF.js / Tauri", "PDF 与桌面壳", "DEFERRED"],
];

export function Overview({ onNavigate }: OverviewProps) {
  return (
    <div className="overview page-enter">
      <section className="hero-grid">
        <div className="hero-copy">
          <div className="section-kicker"><Sparkles size={14} /> STAGE 0 · INTERACTION PROTOTYPE</div>
          <h1>让复杂执行<br /><span>变得可理解。</span></h1>
          <p>
            JobSlayer Workbench 把工作流创作、执行观察、确定性验证和制品审查组织成一个连续的工程循环。
            本原型只演示交互意图，不连接 Agent，也不拥有工作流状态。
          </p>
          <div className="hero-actions">
            <button className="button button-primary" onClick={() => onNavigate("workflow")}>
              进入示范工作台 <ArrowRight size={16} />
            </button>
            <button className="button button-quiet" onClick={() => onNavigate("artifact")}>
              查看人工审查
            </button>
          </div>
          <div className="principle-line">
            <span><ShieldCheck size={15} /> Kernel owns state</span>
            <span><Database size={15} /> Evidence before claims</span>
            <span><Braces size={15} /> Provider-neutral contracts</span>
          </div>
        </div>

        <div className="architecture-card" aria-label="界面与控制面边界">
          <div className="architecture-head">
            <span>AUTHORITY BOUNDARY</span>
            <span className="status-chip status-success"><span className="status-dot" /> EXPLICIT</span>
          </div>
          <div className="client-row">
            <div className="client-tile active"><Boxes size={18} /><span>Web Workbench</span><small>human UI</small></div>
            <div className="client-tile"><Braces size={18} /><span>CLI</span><small>automation</small></div>
            <div className="client-tile"><GitBranch size={18} /><span>SDK / API</span><small>integration</small></div>
          </div>
          <div className="contract-bridge">
            <span>READ MODELS</span>
            <div />
            <strong>CONTROL PLANE CONTRACT</strong>
            <div />
            <span>COMMANDS</span>
          </div>
          <div className="owned-system">
            <div className="owned-title"><ShieldCheck size={19} /><span>JobSlayer-owned engineering truth</span></div>
            <div className="owned-grid">
              <span>WorkflowKernel</span><span>Permissions</span><span>Verification</span>
              <span>Audit chain</span><span>Retry policy</span><span>Completion gate</span>
            </div>
          </div>
          <p className="boundary-note">This prototype ends at the contract line. No command crosses it.</p>
        </div>
      </section>

      <section className="content-section">
        <div className="section-heading">
          <div><span className="section-index">01 / CORE LOOP</span><h2>四个意图，一条工作流</h2></div>
          <p>从创作到复盘保持上下文连续；每个示例都可从左侧导航独立打开。</p>
        </div>
        <div className="demo-grid">
          {demos.map((demo) => {
            const Icon = demo.icon;
            return (
              <button className="demo-card" key={demo.id} onClick={() => onNavigate(demo.id)}>
                <span className="demo-number">{demo.number}</span>
                <span className="demo-icon"><Icon size={21} /></span>
                <span className="demo-eyebrow">{demo.eyebrow}</span>
                <strong>{demo.title}</strong>
                <span className="demo-summary">{demo.summary}</span>
                <span className="demo-link">打开示例 <ArrowRight size={14} /></span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="content-section split-section">
        <div className="intent-panel">
          <span className="section-index">02 / DESIGN INTENT</span>
          <h2>不是聊天窗口，也不是 CRUD 后台</h2>
          <div className="intent-list">
            <div><CheckCircle2 size={17} /><span><strong>结构化反馈</strong>运行 → 任务 → Agent 调用 → 工具 → 验证 → 制品。</span></div>
            <div><CheckCircle2 size={17} /><span><strong>验证可见</strong>“Agent 已完成”与“结果已验证”始终是两个状态。</span></div>
            <div><CheckCircle2 size={17} /><span><strong>血缘可追溯</strong>模型、工具、读取文件、版本、worker 与人工动作可以关联。</span></div>
            <div><CheckCircle2 size={17} /><span><strong>操作经治理</strong>界面提交意图，权限与状态变更仍由控制平面裁决。</span></div>
          </div>
        </div>
        <div className="stack-panel">
          <div className="stack-head"><span>LIBRARY ADOPTION</span><span>Stage 0</span></div>
          {stack.map(([name, purpose, status]) => (
            <div className="stack-row" key={name}>
              <span><strong>{name}</strong><small>{purpose}</small></span>
              <span className={`adoption ${status === "ADOPTED" ? "adopted" : "deferred"}`}>{status}</span>
            </div>
          ))}
          <p><Network size={15} /> 外部 UI 库只负责通用交互；Workflow IR、事件、验证、权限和制品契约仍由 JobSlayer 拥有。</p>
        </div>
      </section>
    </div>
  );
}
