import { useMemo, useState } from "react";
import {
  Activity,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Clock3,
  FileCode2,
  Pause,
  Play,
  RotateCcw,
  ShieldCheck,
  SquareTerminal,
  Wrench,
} from "lucide-react";
import { executionEvents, runTasks } from "../mockData";
import { EChart } from "./EChart";
import { TerminalPanel } from "./TerminalPanel";

interface RunInspectorProps {
  onNotice: (message: string) => void;
}

export function RunInspector({ onNotice }: RunInspectorProps) {
  const [selectedTask, setSelectedTask] = useState("task-implement");
  const [live, setLive] = useState(true);
  const [bottomTab, setBottomTab] = useState<"events" | "terminal" | "validation">("events");
  const task = runTasks.find((item) => item.id === selectedTask) ?? runTasks[0];

  const traceOption = useMemo(() => ({
    animation: false,
    grid: { left: 106, right: 24, top: 22, bottom: 28 },
    xAxis: { type: "value", max: 150, axisLabel: { color: "#7e8490", formatter: "{value}s" }, splitLine: { lineStyle: { color: "#242830" } }, axisLine: { show: false } },
    yAxis: { type: "category", inverse: true, data: ["Planning", "Implementation", "Agent call", "Repository read", "Typecheck"], axisLabel: { color: "#a9afba", fontSize: 11 }, axisLine: { show: false }, axisTick: { show: false } },
    series: [{
      type: "bar",
      barWidth: 13,
      data: [
        { value: 8.3, itemStyle: { color: "#7b8696" } },
        { value: 132.7, itemStyle: { color: "#b9ff66" } },
        { value: 45, itemStyle: { color: "#66715f" } },
        { value: 2.1, itemStyle: { color: "#66715f" } },
        { value: 36.4, itemStyle: { color: "#66715f" } },
      ],
      label: { show: true, position: "right", color: "#cbd0d8", formatter: "{c}s", fontSize: 10 },
    }],
    tooltip: { trigger: "item", backgroundColor: "#15181d", borderColor: "#353a44", textStyle: { color: "#e7e9ed" } },
  }), []);

  return (
    <div className="workbench-page page-enter">
      <header className="page-titlebar run-titlebar">
        <div><span className="section-index">RUN / RUN_UI_028</span><h1>Implement UI framework <span className="status-chip status-running"><span className="status-dot" /> RUNNING</span></h1></div>
        <div className="run-stats"><span><small>ELAPSED</small><strong>02:21</strong></span><span><small>TOKENS</small><strong>18.4K</strong></span><span><small>COST</small><strong>$0.42</strong></span></div>
        <button className="icon-button" aria-label={live ? "暂停模拟直播" : "继续模拟直播"} onClick={() => setLive((value) => !value)}>{live ? <Pause size={16} /> : <Play size={16} />}</button>
      </header>

      <div className="prototype-banner"><Activity size={15} /><span><strong>{live ? "MOCK STREAM · LIVE" : "MOCK STREAM · PAUSED"}</strong> 以下事件来自固定样例，不建立 WebSocket，也不推断真实运行状态。</span></div>

      <div className="run-layout">
        <aside className="task-tree panel-surface">
          <div className="panel-label">EXECUTION HIERARCHY</div>
          <div className="run-root"><span className="tree-status running" /><div><strong>Run UI framework</strong><small>run_ui_028 · 4 tasks</small></div></div>
          <div className="tree-children">
            {runTasks.map((item, index) => (
              <button className={selectedTask === item.id ? "active" : ""} key={item.id} onClick={() => setSelectedTask(item.id)}>
                <span className={`tree-status ${item.status}`} />
                <span><strong>{item.title}</strong><small>{item.owner} · {item.duration}</small></span>
                <ChevronRight size={14} />
                {index < runTasks.length - 1 && <i />}
              </button>
            ))}
          </div>
        </aside>

        <main className="run-main">
          <section className="task-focus panel-surface">
            <div className="task-focus-head">
              <div><span className="section-index">CURRENT TASK · {task.id}</span><h2>{task.title}</h2><p>{task.detail}</p></div>
              <span className={`large-status ${task.status}`}>{task.status}</span>
            </div>
            <div className="activity-steps">
              <div className="done"><CheckCircle2 size={16} /><span><strong>Agent invocation</strong><small>coder-02 · model adapter normalized</small></span><b>45.0s</b></div>
              <div className="done"><FileCode2 size={16} /><span><strong>Repository read</strong><small>24 files · content hashes retained</small></span><b>2.1s</b></div>
              <div className="done"><Wrench size={16} /><span><strong>Patch generated</strong><small>7 files · allowed path policy</small></span><b>38.6s</b></div>
              <div className="current"><CircleDashed size={16} /><span><strong>TypeScript contract check</strong><small>validation.started · attempt 1 / 2</small></span><b>running</b></div>
            </div>
          </section>

          <section className="trace-card panel-surface">
            <div className="panel-heading-inline"><div><span className="panel-label">TRACE WATERFALL</span><h3>Where the time went</h3></div><span>OpenTelemetry-inspired mock</span></div>
            <EChart option={traceOption} height={230} label="当前运行各步骤耗时条形图" />
          </section>

          <section className="bottom-console panel-surface">
            <div className="console-tabs" role="tablist" aria-label="运行详情面板">
              <button role="tab" aria-selected={bottomTab === "events"} className={bottomTab === "events" ? "active" : ""} onClick={() => setBottomTab("events")}><Activity size={14} /> Events <span>{executionEvents.length}</span></button>
              <button role="tab" aria-selected={bottomTab === "terminal"} className={bottomTab === "terminal" ? "active" : ""} onClick={() => setBottomTab("terminal")}><SquareTerminal size={14} /> Terminal</button>
              <button role="tab" aria-selected={bottomTab === "validation"} className={bottomTab === "validation" ? "active" : ""} onClick={() => setBottomTab("validation")}><ShieldCheck size={14} /> Validation <span>3</span></button>
            </div>
            {bottomTab === "events" && (
              <div className="event-table">
                {executionEvents.map((event) => (
                  <div className="event-row" key={event.id}>
                    <span className={`event-level ${event.level}`} />
                    <time>{event.time}</time><code>{event.type}</code><span>{event.task}</span><p>{event.summary}</p><small>{event.id}</small>
                  </div>
                ))}
              </div>
            )}
            {bottomTab === "terminal" && <TerminalPanel />}
            {bottomTab === "validation" && (
              <div className="validation-list">
                <div><CheckCircle2 size={16} /><span><strong>Workflow schema</strong><small>canonical IR v12</small></span><b>PASS</b></div>
                <div><CheckCircle2 size={16} /><span><strong>Allowed path policy</strong><small>7 changed files admitted</small></span><b>PASS</b></div>
                <div><Clock3 size={16} /><span><strong>Unified repository check</strong><small>awaiting generated patch</small></span><b className="pending">WAIT</b></div>
              </div>
            )}
          </section>
        </main>

        <aside className="run-context panel-surface">
          <div className="panel-label">RUN CONTEXT</div>
          <div className="context-section"><span>ASSIGNMENT</span><div><Bot size={15} /><p><strong>coder-02</strong><small>capability: coding</small></p></div><div><GitBranchIcon /><p><strong>windows-dev-01</strong><small>lease expires 14:22</small></p></div></div>
          <div className="context-section"><span>BUDGET</span><div className="budget-label"><span>Tokens</span><b>18.4K / 32K</b></div><div className="progress-track"><i style={{ width: "57.5%" }} /></div><div className="budget-label"><span>Attempts</span><b>1 / 2</b></div><div className="progress-track"><i style={{ width: "50%" }} /></div></div>
          <div className="context-section"><span>PROVENANCE</span><dl><dt>Workflow</dt><dd>wf_ui_v12</dd><dt>Base commit</dt><dd>8e71ad2</dd><dt>Context hash</dt><dd>c2e94a…91bf</dd><dt>Raw logs</dt><dd>2 artifacts</dd></dl></div>
          <button className="button button-danger-quiet" onClick={() => onNotice("演示模式：已生成“请求重试”的本地反馈，但未改变任务、预算或重试策略。 ")}><RotateCcw size={15} /> 请求重试</button>
          <p className="context-footnote">按钮表达用户意图；真正的重试必须经过权限、预算、当前状态和 Kernel transition 检查。</p>
        </aside>
      </div>
    </div>
  );
}

function GitBranchIcon() {
  return <span className="worker-icon"><SquareTerminal size={15} /></span>;
}
