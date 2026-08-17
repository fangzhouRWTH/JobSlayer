import { useMemo } from "react";
import {
  Activity,
  Bot,
  CheckCircle2,
  CircleDollarSign,
  Clock3,
  Cpu,
  Database,
  Gauge,
  Server,
  ShieldCheck,
  TriangleAlert,
  UserCheck,
  Wrench,
} from "lucide-react";
import { EChart } from "./EChart";

const metrics = [
  { label: "Run success", value: "94.2%", delta: "+2.8%", icon: CheckCircle2, tone: "success" },
  { label: "P95 latency", value: "4m 18s", delta: "−18s", icon: Clock3, tone: "neutral" },
  { label: "Validation fail", value: "7.1%", delta: "+0.6%", icon: ShieldCheck, tone: "warning" },
  { label: "Human gates", value: "31", delta: "12 pending", icon: UserCheck, tone: "neutral" },
];

export function Observability() {
  const trendOption = useMemo(() => ({
    animationDuration: 500,
    grid: { left: 36, right: 20, top: 28, bottom: 28 },
    tooltip: { trigger: "axis", backgroundColor: "#15181d", borderColor: "#363b45", textStyle: { color: "#e7e9ed" } },
    xAxis: { type: "category", boundaryGap: false, data: ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"], axisLabel: { color: "#747b87" }, axisLine: { lineStyle: { color: "#30343d" } } },
    yAxis: { type: "value", min: 75, max: 100, axisLabel: { color: "#747b87", formatter: "{value}%" }, splitLine: { lineStyle: { color: "#242830" } } },
    series: [
      { name: "Success", type: "line", smooth: true, symbol: "none", data: [88, 91, 90, 94, 93, 96, 94], lineStyle: { color: "#b9ff66", width: 2 }, areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "rgba(185,255,102,.24)" }, { offset: 1, color: "rgba(185,255,102,0)" }] } } },
      { name: "Validated", type: "line", smooth: true, symbol: "none", data: [82, 85, 87, 89, 91, 92, 93], lineStyle: { color: "#7e8cff", width: 2 } },
    ],
  }), []);

  const costOption = useMemo(() => ({
    animationDuration: 450,
    tooltip: { trigger: "item", backgroundColor: "#15181d", borderColor: "#363b45", textStyle: { color: "#e7e9ed" } },
    series: [{
      type: "pie",
      radius: ["60%", "82%"],
      center: ["50%", "47%"],
      avoidLabelOverlap: true,
      label: { color: "#aeb4bd", formatter: "{b}\n{d}%", fontSize: 10 },
      labelLine: { lineStyle: { color: "#4a505b" } },
      data: [
        { name: "Coding", value: 52, itemStyle: { color: "#b9ff66" } },
        { name: "Planning", value: 18, itemStyle: { color: "#7e8cff" } },
        { name: "Review", value: 17, itemStyle: { color: "#f2c86b" } },
        { name: "Tools", value: 13, itemStyle: { color: "#67707d" } },
      ],
    }],
  }), []);

  return (
    <div className="workbench-page observability-page page-enter">
      <header className="page-titlebar">
        <div><span className="section-index">OBSERVABILITY / LAST 24 HOURS</span><h1>Operations & Evidence</h1></div>
        <div className="page-actions"><button className="button button-quiet"><Gauge size={15} /> All workflows</button><button className="button button-quiet"><Clock3 size={15} /> 24 hours</button></div>
      </header>

      <div className="prototype-banner"><Database size={15} /><span><strong>固定演示数据</strong> 图表不读取遥测服务；字段展示遵循“稳定标量、不包含 prompt、凭据与原始日志”的意图。</span></div>

      <div className="metric-grid">
        {metrics.map((metric) => { const Icon = metric.icon; return (
          <article className="metric-card panel-surface" key={metric.label}>
            <span className={`metric-icon ${metric.tone}`}><Icon size={18} /></span><span className="metric-label">{metric.label}</span>
            <strong>{metric.value}</strong><small className={metric.tone}>{metric.delta}</small>
          </article>
        ); })}
      </div>

      <div className="charts-grid">
        <section className="panel-surface chart-panel chart-wide">
          <div className="panel-heading-inline"><div><span className="panel-label">OUTCOME SIGNALS</span><h3>Completed vs. validated</h3></div><div className="chart-legend"><span className="lime" /> Run success <span className="violet" /> Validated output</div></div>
          <EChart option={trendOption} height={285} label="运行成功与输出验证趋势图" />
        </section>
        <section className="panel-surface chart-panel">
          <div className="panel-heading-inline"><div><span className="panel-label">MODEL COST</span><h3>$18.42 today</h3></div><CircleDollarSign size={19} /></div>
          <EChart option={costOption} height={285} label="模型成本按工作类型分布图" />
        </section>
      </div>

      <div className="ops-grid">
        <section className="panel-surface trace-list-card">
          <div className="panel-heading-inline"><div><span className="panel-label">DISTRIBUTED TRACE</span><h3>run_ui_028</h3></div><span className="status-chip status-running"><span className="status-dot" /> RUNNING</span></div>
          <div className="trace-list">
            <div><Activity size={15} /><span><strong>Run</strong><small>run_ui_028</small></span><i><b style={{ width: "93%" }} /></i><em>141.0s</em></div>
            <div className="nested"><Bot size={15} /><span><strong>Agent invocation</strong><small>coder-02</small></span><i><b style={{ width: "46%" }} /></i><em>45.0s</em></div>
            <div className="nested-2"><Wrench size={15} /><span><strong>Repository read</strong><small>tool.completed</small></span><i><b style={{ width: "12%" }} /></i><em>2.1s</em></div>
            <div className="nested-2"><ShieldCheck size={15} /><span><strong>Typecheck</strong><small>validation.started</small></span><i><b className="running" style={{ width: "68%" }} /></i><em>36.4s</em></div>
          </div>
        </section>

        <section className="panel-surface workers-card">
          <div className="panel-heading-inline"><div><span className="panel-label">WORKERS</span><h3>Execution capacity</h3></div><span>2 / 3 online</span></div>
          <div className="worker-row"><span className="worker-health online" /><Server size={16} /><span><strong>windows-dev-01</strong><small>Windows · CPU · coding</small></span><b>68%</b></div>
          <div className="worker-row"><span className="worker-health online" /><Cpu size={16} /><span><strong>gpu-linux-01</strong><small>Linux · RTX 4080 · simulation</small></span><b>24%</b></div>
          <div className="worker-row"><span className="worker-health offline" /><Server size={16} /><span><strong>ci-worker-02</strong><small>Linux · build · disconnected</small></span><b>—</b></div>
        </section>

        <section className="panel-surface attention-card">
          <div className="panel-heading-inline"><div><span className="panel-label">ACTIONABLE ONLY</span><h3>Needs attention</h3></div><span className="attention-count">3</span></div>
          <div><TriangleAlert size={16} /><span><strong>Human approval required</strong><small>run_api_114 · 7m ago</small></span></div>
          <div><ShieldCheck size={16} /><span><strong>Validation failed</strong><small>run_graphics_041 · shader compile</small></span></div>
          <div><Server size={16} /><span><strong>Worker disconnected</strong><small>ci-worker-02 · lease expired</small></span></div>
        </section>
      </div>
    </div>
  );
}
