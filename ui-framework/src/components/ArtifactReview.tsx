import { useState } from "react";
import { DiffEditor } from "@monaco-editor/react";
import ReactMarkdown from "react-markdown";
import {
  Braces,
  Check,
  CheckCircle2,
  ChevronRight,
  Code2,
  FileText,
  GitCompareArrows,
  History,
  MessageSquareText,
  RotateCcw,
  ShieldCheck,
  TriangleAlert,
  UserCheck,
  X,
} from "lucide-react";
import { artifacts, modifiedCode, originalCode, reportMarkdown } from "../mockData";

interface ArtifactReviewProps {
  onNotice: (message: string) => void;
}

export function ArtifactReview({ onNotice }: ArtifactReviewProps) {
  const [selectedId, setSelectedId] = useState(artifacts[0].id);
  const [tab, setTab] = useState<"preview" | "diff" | "metadata">("preview");
  const [decision, setDecision] = useState<"pending" | "approved" | "rejected" | "revision">("pending");
  const selected = artifacts.find((artifact) => artifact.id === selectedId) ?? artifacts[0];

  const chooseDecision = (next: typeof decision) => {
    setDecision(next);
    onNotice(`本地审查示例已切换为 ${next}；未创建决定记录，也未调用 WorkflowKernel。`);
  };

  return (
    <div className="workbench-page artifact-page page-enter">
      <header className="page-titlebar">
        <div><span className="section-index">RUN_UI_028 / REVIEW GATE</span><h1>Artifact Review</h1></div>
        <div className="review-identity"><span className="avatar">FR</span><span><small>AUTHORIZED ACTOR · MOCK</small><strong>Franz · reviewer</strong></span></div>
      </header>

      <div className="prototype-banner"><ShieldCheck size={15} /><span><strong>审查语义示范</strong> 验证与授权条件被显式展示；所有决定仅保存在当前组件状态，刷新即消失。</span></div>

      <div className="artifact-layout">
        <aside className="artifact-list panel-surface">
          <div className="panel-label">ARTIFACTS · 3</div>
          <label className="artifact-search"><span className="sr-only">筛选制品</span><input placeholder="Filter artifacts…" /></label>
          {artifacts.map((artifact) => {
            const Icon = artifact.type === "markdown" ? FileText : artifact.type === "diff" ? GitCompareArrows : Braces;
            return (
              <button key={artifact.id} className={selectedId === artifact.id ? "active" : ""} onClick={() => { setSelectedId(artifact.id); setTab(artifact.type === "diff" ? "diff" : "preview"); }}>
                <span className="artifact-file-icon"><Icon size={17} /></span>
                <span><strong>{artifact.name}</strong><small>{artifact.id} · {artifact.size}</small></span>
                <span className={`tiny-state ${artifact.validation}`}>{artifact.validation}</span>
                <ChevronRight size={14} />
              </button>
            );
          })}
          <div className="artifact-chain"><History size={15} /><span><strong>3 versions retained</strong><small>Content-addressed · append-only manifest</small></span></div>
        </aside>

        <main className="artifact-main panel-surface">
          <div className="artifact-toolbar">
            <div><span className="section-index">{selected.type.toUpperCase()} · {selected.version}</span><h2>{selected.name}</h2></div>
            <div className="canvas-tabs" role="tablist" aria-label="制品查看模式">
              <button role="tab" aria-selected={tab === "preview"} className={tab === "preview" ? "active" : ""} onClick={() => setTab("preview")}>Preview</button>
              <button role="tab" aria-selected={tab === "diff"} className={tab === "diff" ? "active" : ""} onClick={() => setTab("diff")}>Diff</button>
              <button role="tab" aria-selected={tab === "metadata"} className={tab === "metadata" ? "active" : ""} onClick={() => setTab("metadata")}>Metadata</button>
            </div>
          </div>

          <div className="artifact-content">
            {tab === "preview" && (
              selected.type === "json" ? (
                <pre className="json-preview">{JSON.stringify({
                  event_id: "evt_10205",
                  run_id: "run_ui_028",
                  task_id: "task-implement",
                  agent_id: "coder-02",
                  timestamp: "2026-08-12T14:04:23.114Z",
                  type: "validation.started",
                  raw_log_artifact_id: "artifact_raw_092",
                }, null, 2)}</pre>
              ) : <article className="markdown-preview"><ReactMarkdown>{reportMarkdown}</ReactMarkdown></article>
            )}
            {tab === "diff" && (
              <div className="diff-wrap">
                <div className="diff-caption"><Code2 size={14} /><span>src/example/approval.ts</span><span>4 additions · 3 deletions</span></div>
                <DiffEditor original={originalCode} modified={modifiedCode} language="typescript" theme="vs-dark" options={{ readOnly: true, renderSideBySide: true, minimap: { enabled: false }, fontSize: 12, lineHeight: 21, scrollBeyondLastLine: false, automaticLayout: true }} />
              </div>
            )}
            {tab === "metadata" && (
              <div className="metadata-view">
                <div><span>Artifact ID</span><code>{selected.id}</code></div>
                <div><span>Content type</span><code>{selected.type}</code></div>
                <div><span>Producer</span><code>{selected.producer}</code></div>
                <div><span>Related task</span><code>{selected.relatedTask}</code></div>
                <div><span>Version</span><code>{selected.version}</code></div>
                <div className="metadata-wide"><span>SHA-256</span><code>{selected.sha256}</code></div>
                <div className="metadata-wide"><span>Validation</span><strong className={selected.validation === "passed" ? "text-success" : "text-warning"}>{selected.validation.toUpperCase()}</strong></div>
              </div>
            )}
          </div>
        </main>

        <aside className="review-panel panel-surface">
          <div className="panel-label">HUMAN GATE</div>
          <div className={`decision-state ${decision}`}>
            {decision === "pending" ? <UserCheck size={22} /> : decision === "approved" ? <Check size={22} /> : decision === "rejected" ? <X size={22} /> : <RotateCcw size={22} />}
            <div><span>DEMO DECISION</span><strong>{decision}</strong></div>
          </div>

          <div className="gate-checks">
            <div><CheckCircle2 size={16} /><span><strong>Verification report</strong><small>PASS · profile ui-stage0-v1</small></span></div>
            <div><CheckCircle2 size={16} /><span><strong>Artifact integrity</strong><small>3 / 3 SHA-256 verified</small></span></div>
            <div><CheckCircle2 size={16} /><span><strong>Approval authority</strong><small>reviewer · valid for 08m</small></span></div>
          </div>

          <div className="risk-box"><TriangleAlert size={16} /><p><strong>Risk · medium</strong>原型证明交互结构，不证明真实 API、安全响应头或并发决定行为。</p></div>
          <label className="rationale-label">Decision rationale<textarea defaultValue="验证已通过；界面清楚地区分展示状态、命令意图与 Kernel 所有权。" /></label>
          <div className="decision-actions">
            <button className="button approve" onClick={() => chooseDecision("approved")}><Check size={15} /> Approve</button>
            <button className="button reject" onClick={() => chooseDecision("rejected")}><X size={15} /> Reject</button>
            <button className="button revision" onClick={() => chooseDecision("revision")}><MessageSquareText size={15} /> Request revision</button>
          </div>
          <p className="context-footnote">真实批准只能提交绑定 card hash 的命令；即使批准，也不能跳过 Integrating 与完成证据。</p>
        </aside>
      </div>
    </div>
  );
}
