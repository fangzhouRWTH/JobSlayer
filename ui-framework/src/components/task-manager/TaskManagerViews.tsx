import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  CirclePlus,
  Clock3,
  Gauge,
  GitBranch,
  ListTodo,
  LoaderCircle,
  LockKeyhole,
  MessageSquareText,
  Network,
  RefreshCw,
  RotateCcw,
  Send,
  ShieldCheck,
  Square,
  Terminal,
  Zap,
} from "lucide-react";
import type {
  ActiveSemanticUIDesign,
  ManagedTaskDetail,
  ManagedTaskSummary,
  QuickAgentCapacitySnapshot,
  QuickAgentMode,
  QuickAgentModelCatalogSnapshot,
  QuickAgentSessionSnapshot,
  TaskManagerHumanActionGuidance,
  TaskManagerHumanDecisionOption,
  TaskManagerSession,
} from "../../types";
import type { TaskManagerViewId } from "./TaskManagerRail";
import { HumanActionGuidanceCard } from "./HumanActionGuidanceCard";

const stageLabels: Record<ManagedTaskSummary["stage"], string> = {
  proposal_pending: "候选图待确认",
  planning: "规划中",
  ready: "已固化 · 待执行",
  running: "执行中",
  needs_attention: "需要处理",
  verifying: "验证中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  archived: "已归档",
};

const satisfiedWorkflowStates = new Set([
  "completed",
  "gate_approved",
  "deliverable_accepted",
]);

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

interface CommonViewProps {
  session: TaskManagerSession | null;
  uiDesign: ActiveSemanticUIDesign | null;
  tasks: ManagedTaskSummary[];
  detail: ManagedTaskDetail | null;
  busy: boolean;
  error: string | null;
  onNavigate: (view: TaskManagerViewId) => void;
  onSelectTask: (taskId: string) => void;
  onRefresh: () => void;
  onNewTask: () => void;
  onAdvanceRun: () => void;
  onSubmitHumanDecision: (
    guidance: TaskManagerHumanActionGuidance,
    decision: TaskManagerHumanDecisionOption,
    rationale: string,
  ) => void;
  onRecordHumanFeedback: (
    guidance: TaskManagerHumanActionGuidance,
    decision: TaskManagerHumanDecisionOption,
    content: string,
  ) => void;
  onAskHumanActionAgent: (
    guidance: TaskManagerHumanActionGuidance,
    content: string,
  ) => void;
  canSubmitHumanDecision: (
    guidance: TaskManagerHumanActionGuidance,
    decision: TaskManagerHumanDecisionOption,
  ) => boolean;
  isFormalHumanDecision: (decision: TaskManagerHumanDecisionOption) => boolean;
}

function PageHeader({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children?: React.ReactNode;
}) {
  return (
    <header className="task-view-header">
      <div>
        <span>{eyebrow}</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {children && <div className="task-view-actions">{children}</div>}
    </header>
  );
}

export function TaskManagerHome({
  uiDesign,
  tasks,
  onNavigate,
}: Pick<CommonViewProps, "uiDesign" | "tasks" | "onNavigate">) {
  const activeTasks = tasks.filter((task) => !task.is_archived && task.stage !== "completed");
  const attention = tasks.filter((task) => task.blocker_count > 0 || task.stage === "needs_attention");
  return (
    <section className="task-view-page task-home-view page-enter">
      <div className="task-home-hero">
        <div className="task-home-mark" aria-hidden="true"><span>JS</span></div>
        <div>
          <span className="task-view-kicker">ENGINEERING CONTROL PLANE</span>
          <h1>JobSlayer</h1>
          <p>
            用可审计任务图组织讨论、执行与反馈。Agent 提出方案，JobSlayer 掌握工作流状态、
            验证要求与完成判定。
          </p>
        </div>
      </div>

      <div className="task-home-metrics" aria-label="当前系统摘要">
        <article><strong>{tasks.length}</strong><span>任务记录</span><small>包含历史与归档</small></article>
        <article><strong>{activeTasks.length}</strong><span>活动任务</span><small>仍在闭环中的任务</small></article>
        <article><strong>{attention.length}</strong><span>需要关注</span><small>阻塞或需人工介入</small></article>
      </div>

      <div className="task-home-grid">
        <button type="button" onClick={() => onNavigate("agent")}>
          <Bot size={22} /><span><strong>Codex / Agent</strong><small>适配器、能力与执行引用</small></span><ArrowRight size={17} />
        </button>
        <button type="button" onClick={() => onNavigate("control")}>
          <ListTodo size={22} /><span><strong>任务 Backlog / 总控</strong><small>跨任务检查阻塞、积压和事件</small></span><ArrowRight size={17} />
        </button>
        <button type="button" onClick={() => onNavigate("orchestration")}>
          <Network size={22} /><span><strong>任务编排</strong><small>在 DAG 与 Agent 对话中具体化任务</small></span><ArrowRight size={17} />
        </button>
        <button type="button" onClick={() => onNavigate("execution")}>
          <Activity size={22} /><span><strong>具体任务执行</strong><small>检查 Kernel 状态、反馈与证据</small></span><ArrowRight size={17} />
        </button>
      </div>

      <footer className="task-home-foot">
        <ShieldCheck size={14} />
        <span>
          活动设计：{uiDesign
            ? `${uiDesign.binding.scheme_id} · V${uiDesign.binding.revision}`
            : "等待后端 SUID binding"}
        </span>
      </footer>
    </section>
  );
}

async function quickAgentApi<T>(
  path: string,
  token: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const headers: Record<string, string> = { "X-JobSlayer-Session": token };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`/api/task-manager/quick-agent${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const payload = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`);
  return payload as T;
}

function durationUntil(value: string | null, now: number): string {
  if (!value) return "刷新时间未提供";
  const milliseconds = new Date(value).getTime() - now;
  if (!Number.isFinite(milliseconds)) return "刷新时间不可解析";
  if (milliseconds <= 0) return "即将刷新";
  const totalMinutes = Math.ceil(milliseconds / 60_000);
  const days = Math.floor(totalMinutes / 1_440);
  const hours = Math.floor((totalMinutes % 1_440) / 60);
  const minutes = totalMinutes % 60;
  return [days ? `${days}天` : "", hours ? `${hours}小时` : "", `${minutes}分钟`]
    .filter(Boolean)
    .join(" ");
}

const quickAgentStateLabels: Record<QuickAgentSessionSnapshot["state"], string> = {
  idle: "待命",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已中断",
  timed_out: "已超时",
};

export function TaskManagerAgentStatus({
  session,
}: Pick<CommonViewProps, "session">) {
  const [agent, setAgent] = useState<QuickAgentSessionSnapshot | null>(null);
  const [capacity, setCapacity] = useState<QuickAgentCapacitySnapshot | null>(null);
  const [catalog, setCatalog] = useState<QuickAgentModelCatalogSnapshot | null>(null);
  const [mode, setMode] = useState<QuickAgentMode>("discuss");
  const [selectedModel, setSelectedModel] = useState("");
  const [selectedEffort, setSelectedEffort] = useState("");
  const [selectedServiceTier, setSelectedServiceTier] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const consoleEnd = useRef<HTMLDivElement>(null);
  const configuredConversation = useRef<string | null>(null);
  const available = Boolean(session?.capabilities.quick_agent_discussion);
  const token = session?.submission_token;

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!available || !token) return undefined;
    let active = true;
    const loadSession = async () => {
      try {
        const snapshot = await quickAgentApi<QuickAgentSessionSnapshot>("/session", token);
        if (active) setAgent(snapshot);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : String(cause));
      }
    };
    const loadCapacity = async () => {
      try {
        const snapshot = await quickAgentApi<QuickAgentCapacitySnapshot>("/capacity", token);
        if (active) setCapacity(snapshot);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : String(cause));
      }
    };
    const loadModels = async () => {
      try {
        const snapshot = await quickAgentApi<QuickAgentModelCatalogSnapshot>("/models", token);
        if (active) setCatalog(snapshot);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : String(cause));
      }
    };
    void Promise.all([loadSession(), loadCapacity(), loadModels()]);
    const sessionTimer = window.setInterval(() => void loadSession(), 750);
    const capacityTimer = window.setInterval(() => void loadCapacity(), 30_000);
    return () => {
      active = false;
      window.clearInterval(sessionTimer);
      window.clearInterval(capacityTimer);
    };
  }, [available, token]);

  useEffect(() => {
    if (!agent || !catalog?.available) return;
    if (configuredConversation.current === agent.conversation_id) return;
    const current = catalog.models.find((item) => item.model_id === agent.model)
      ?? catalog.models.find((item) => item.is_default)
      ?? catalog.models[0];
    if (!current) return;
    configuredConversation.current = agent.conversation_id;
    setSelectedModel(current.model_id);
    setSelectedEffort(
      current.reasoning_efforts.some((item) => item.effort === agent.reasoning_effort)
        ? agent.reasoning_effort
        : current.default_reasoning_effort
    );
    setSelectedServiceTier(agent.service_tier || "");
  }, [agent, catalog]);

  useEffect(() => {
    consoleEnd.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [agent?.events]);

  const bucket = useMemo(() => (
    capacity?.buckets.find((item) => item.limit_id === "codex")
    ?? capacity?.buckets[0]
    ?? null
  ), [capacity]);
  const primary = bucket?.primary ?? null;
  const secondary = bucket?.secondary ?? null;
  const selectedModelOption = useMemo(() => (
    catalog?.models.find((item) => item.model_id === selectedModel)
    ?? catalog?.models.find((item) => item.is_default)
    ?? catalog?.models[0]
    ?? null
  ), [catalog, selectedModel]);
  const runtimeServiceTierLabel = useMemo(() => {
    if (!agent?.service_tier) return "STANDARD";
    const runtimeModel = catalog?.models.find((item) => item.model_id === agent.model);
    return runtimeModel?.service_tiers
      .find((item) => item.tier_id === agent.service_tier)?.name.toUpperCase()
      ?? agent.service_tier.toUpperCase();
  }, [agent?.model, agent?.service_tier, catalog]);

  const changeModel = (modelId: string) => {
    const option = catalog?.models.find((item) => item.model_id === modelId);
    if (!option) return;
    setSelectedModel(modelId);
    if (!option.reasoning_efforts.some((item) => item.effort === selectedEffort)) {
      setSelectedEffort(option.default_reasoning_effort);
    }
    if (!option.service_tiers.some((item) => item.tier_id === selectedServiceTier)) {
      setSelectedServiceTier("");
    }
  };

  const refreshCapacity = async () => {
    if (!token) return;
    setActionBusy(true);
    setError(null);
    try {
      setCapacity(await quickAgentApi<QuickAgentCapacitySnapshot>("/capacity?refresh=1", token));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setActionBusy(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!token || !message.trim() || agent?.state === "running") return;
    const content = message.trim();
    setMessage("");
    setActionBusy(true);
    setError(null);
    try {
      setAgent(await quickAgentApi<QuickAgentSessionSnapshot>("/messages", token, {
        method: "POST",
        body: {
          content,
          mode,
          model: selectedModelOption?.model_id ?? agent?.model,
          reasoning_effort: selectedEffort || agent?.reasoning_effort,
          service_tier: selectedServiceTier || null,
        },
      }));
    } catch (cause) {
      setMessage(content);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setActionBusy(false);
    }
  };

  const command = async (path: "/cancel" | "/new-session") => {
    if (!token) return;
    setActionBusy(true);
    setError(null);
    try {
      setAgent(await quickAgentApi<QuickAgentSessionSnapshot>(path, token, {
        method: "POST",
        body: {},
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setActionBusy(false);
    }
  };

  return (
    <section className="task-view-page quick-agent-view page-enter">
      <PageHeader
        eyebrow="LOCAL CODEX / QUICK SESSION"
        title="Codex Quick Agent"
        description="直接讨论或快速处理当前仓库；该会话独立于任务 DAG、执行 Run 与完成判定。"
      >
        <button className="button button-quiet" type="button" disabled={!available || actionBusy} onClick={() => void command("/new-session")}><RotateCcw size={14} /> 新会话</button>
      </PageHeader>

      {!available ? (
        <section className="task-view-card quick-agent-unavailable">
          <LockKeyhole size={34} />
          <h2>Quick Agent 未启用</h2>
          <p>通过桌面入口启动，或为 API 添加 <code>--allow-quick-agent</code> 并使用含 <code>quick-agent</code> 角色的本地签名会话。</p>
        </section>
      ) : (
        <div className="quick-agent-layout">
          <aside className="quick-agent-sidebar">
            <article className="task-view-card quick-capacity-card">
              <header><Gauge size={18} /><span>当前剩余容量</span><button type="button" title="立即查询 Codex" aria-label="立即查询 Codex 容量" disabled={actionBusy} onClick={() => void refreshCapacity()}><RefreshCw size={15} /></button></header>
              {capacity?.available && primary ? (
                <>
                  <strong>{primary.remaining_percent}<small>%</small></strong>
                  <div className="quick-capacity-meter"><i style={{ width: `${primary.remaining_percent}%` }} /></div>
                  <p>{bucket?.limit_name ?? bucket?.limit_id} · {bucket?.plan_type ?? "plan unknown"}</p>
                  <dl>
                    <div><dt>下次刷新</dt><dd>{durationUntil(primary.resets_at, now)}</dd></div>
                    <div><dt>具体时间</dt><dd>{primary.resets_at ? formatTime(primary.resets_at) : "未提供"}</dd></div>
                    {secondary && <div><dt>次级窗口</dt><dd>{secondary.remaining_percent}% · {durationUntil(secondary.resets_at, now)}</dd></div>}
                  </dl>
                  <small>Codex 原始数据 · {formatTime(capacity.observed_at)}</small>
                </>
              ) : (
                <div className="quick-capacity-error"><AlertTriangle size={18} /><p>{capacity?.error ?? "正在读取 Codex 容量…"}</p></div>
              )}
            </article>

            <article className="task-view-card quick-runtime-card">
              <header><Terminal size={17} /><span>本机会话</span></header>
              <dl>
                <div><dt>状态</dt><dd className={`quick-state state-${agent?.state ?? "idle"}`}><i /> {agent ? quickAgentStateLabels[agent.state] : "连接中"}</dd></div>
                <div><dt>模型</dt><dd>{agent?.model ?? "—"}</dd></div>
                <div><dt>推理强度</dt><dd>{agent?.reasoning_effort?.toUpperCase() ?? "—"}</dd></div>
                <div><dt>速度</dt><dd>{runtimeServiceTierLabel}</dd></div>
                <div><dt>单轮上限</dt><dd>{agent ? `${Math.round(agent.maximum_turn_seconds / 60)} 分钟` : "—"}</dd></div>
              </dl>
              <p title={agent?.workspace_root}>{agent?.workspace_root ?? "等待工作区信息"}</p>
            </article>

            <div className="task-view-boundary quick-agent-boundary">
              <ShieldCheck size={16} />
              <p>讨论模式只读；快速执行可写当前仓库但默认禁网、无自动审批。输出不会写入任务链，也不代表工作流完成。</p>
            </div>
          </aside>

          <section className="task-view-card quick-console">
            <header className="quick-console-header">
              <span><Terminal size={16} /> AGENT CONSOLE</span>
              <small>{agent?.thread_id ? `THREAD ${agent.thread_id.slice(0, 10)}` : "NEW THREAD"}</small>
            </header>
            <div className="quick-console-stream" aria-live="polite">
              {!agent?.events.length && (
                <div className="quick-console-empty"><Bot size={30} /><h2>开始一个独立 Codex 会话</h2><p>适合快速问答、查看代码和短程修改。复杂可追踪工作仍应回到任务编排。</p></div>
              )}
              {agent?.events.map((entry) => (
                <article key={entry.sequence} className={`quick-event role-${entry.role}`}>
                  <header><span>{entry.role === "user" ? session?.principal.display_name : entry.role === "agent" ? "Codex" : entry.role === "tool" ? "Tool" : "System"}</span><time>{formatTime(entry.created_at)}</time></header>
                  <pre>{entry.content}</pre>
                </article>
              ))}
              {agent?.state === "running" && <div className="quick-console-running"><LoaderCircle size={15} /> Codex 正在处理并回传事件…</div>}
              <div ref={consoleEnd} />
            </div>

            {error && <div className="quick-console-error" role="alert"><AlertTriangle size={15} /><span>{error}</span><button type="button" onClick={() => setError(null)}>关闭</button></div>}

            <form className="quick-composer" onSubmit={submit}>
              <div className="quick-model-controls" aria-label="Codex 模型配置">
                <label>
                  <span>模型 / 版本</span>
                  <select value={selectedModelOption?.model_id ?? ""} onChange={(event) => changeModel(event.target.value)} disabled={agent?.state === "running" || !catalog?.available}>
                    {!catalog?.available && <option value="">{catalog ? "模型目录不可用" : "正在读取本机目录…"}</option>}
                    {catalog?.models.map((item) => <option key={item.model_id} value={item.model_id}>{item.display_name}</option>)}
                  </select>
                </label>
                <label>
                  <span>推理强度</span>
                  <select value={selectedEffort} onChange={(event) => setSelectedEffort(event.target.value)} disabled={agent?.state === "running" || !selectedModelOption}>
                    {selectedModelOption?.reasoning_efforts.map((item) => <option key={item.effort} value={item.effort}>{item.effort.toUpperCase()}</option>)}
                  </select>
                </label>
                <label>
                  <span>响应速度</span>
                  <select value={selectedServiceTier} onChange={(event) => setSelectedServiceTier(event.target.value)} disabled={agent?.state === "running" || !selectedModelOption}>
                    <option value="">Standard</option>
                    {selectedModelOption?.service_tiers.map((item) => <option key={item.tier_id} value={item.tier_id}>{item.name}</option>)}
                  </select>
                </label>
              </div>
              {catalog && !catalog.available && (
                <div className="quick-model-catalog-error" role="alert"><AlertTriangle size={14} /><span>{catalog.error ?? "Codex 没有返回可用模型目录"}</span></div>
              )}
              {selectedModelOption && (
                <div className="quick-model-detail">
                  <p>{selectedModelOption.description}</p>
                  <div>
                    {selectedModelOption.input_modalities.map((item) => <span key={item}>{item}</span>)}
                    {selectedModelOption.multi_agent_version && <span>Agent {selectedModelOption.multi_agent_version.toUpperCase()}</span>}
                    {selectedModelOption.is_default && <span>本机默认</span>}
                    {selectedServiceTier && <span>更快 · 更多用量</span>}
                  </div>
                  {selectedEffort && <small>{selectedModelOption.reasoning_efforts.find((item) => item.effort === selectedEffort)?.description}</small>}
                </div>
              )}
              <div className="quick-mode-switch" aria-label="Quick Agent 权限模式">
                <button type="button" className={mode === "discuss" ? "active" : ""} aria-pressed={mode === "discuss"} onClick={() => setMode("discuss")} disabled={agent?.state === "running"}><MessageSquareText size={14} /><span>讨论</span><small>只读</small></button>
                <button type="button" className={mode === "execute" ? "active execute" : "execute"} aria-pressed={mode === "execute"} onClick={() => setMode("execute")} disabled={!session?.capabilities.quick_agent_execution || agent?.state === "running"}><Zap size={14} /><span>快速执行</span><small>仓库可写</small></button>
              </div>
              <textarea
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") event.currentTarget.form?.requestSubmit();
                }}
                disabled={agent?.state === "running"}
                placeholder={mode === "discuss" ? "和 Codex 讨论当前仓库…" : "说明需要立即执行的仓库内工作…"}
              />
              <footer>
                <small>{mode === "discuss" ? "只读检查 · Ctrl/⌘ + Enter 发送" : "可修改仓库 · 默认禁网 · Ctrl/⌘ + Enter 发送"}</small>
                {agent?.state === "running" ? (
                  <button className="button button-danger" type="button" disabled={actionBusy} onClick={() => void command("/cancel")}><Square size={13} /> 中断</button>
                ) : (
                  <button className="button button-primary" type="submit" disabled={actionBusy || !message.trim() || !selectedModelOption || !selectedEffort}>{actionBusy ? <LoaderCircle size={14} /> : <Send size={14} />} 发送</button>
                )}
              </footer>
            </form>
          </section>
        </div>
      )}
    </section>
  );
}

export function TaskManagerControl({
  session,
  tasks,
  detail,
  busy,
  onSelectTask,
  onRefresh,
  onNewTask,
  onNavigate,
}: CommonViewProps) {
  const backlog = detail?.backlog ?? [];
  const logs = detail?.log.slice().reverse().slice(0, 6) ?? [];
  return (
    <section className="task-view-page task-control-view page-enter">
      <PageHeader
        eyebrow="PORTFOLIO / CONTROL"
        title="任务 Backlog / 总控"
        description="选择任务并检查积压与最近事件。权威 revision 始终来自后端。"
      >
        <button className="button button-quiet" type="button" disabled={!session || busy} onClick={onRefresh}><RefreshCw size={14} /> 刷新</button>
        <button className="button button-primary" type="button" disabled={!session || busy} onClick={onNewTask}><CirclePlus size={14} /> 新任务</button>
      </PageHeader>

      <div className="task-control-layout">
        <section className="task-view-card task-portfolio">
          <header className="task-view-section-title"><span><ListTodo size={15} /> 全部任务</span><small>{tasks.length} TASKS</small></header>
          <div>
            {tasks.map((task) => (
              <button
                key={task.task_id}
                type="button"
                className={task.task_id === detail?.task.task_id ? "active" : ""}
                aria-pressed={task.task_id === detail?.task.task_id}
                onClick={() => onSelectTask(task.task_id)}
              >
                <span className={`task-stage stage-${task.stage}`}><i /> {stageLabels[task.stage]}</span>
                <strong>{task.title}</strong>
                <small>R{task.revision} · {task.node_count} nodes · {task.backlog_count} backlog</small>
                {task.blocker_count > 0 && <em><AlertTriangle size={11} /> {task.blocker_count}</em>}
              </button>
            ))}
            {!tasks.length && <div className="task-view-empty"><ListTodo size={27} /><p>还没有任务记录。</p></div>}
          </div>
        </section>

        <div className="task-control-detail">
          <section className="task-view-card task-backlog-panel">
            <header className="task-view-section-title">
              <span><GitBranch size={15} /> 当前 Backlog</span>
              <button type="button" disabled={!detail} onClick={() => onNavigate("orchestration")}>打开编排 <ArrowRight size={13} /></button>
            </header>
            <div>
              {backlog.map((item, index) => (
                <article key={item.node_id}>
                  <b>{String(index + 1).padStart(2, "0")}</b>
                  <span><strong>{item.title}</strong><small>{item.reason}</small></span>
                  <i className={`node-state state-${item.state}`}>{item.state.toUpperCase()}</i>
                </article>
              ))}
              {!backlog.length && <div className="task-view-empty"><CheckCircle2 size={27} /><p>{detail ? "当前任务没有积压节点。" : "选择一个任务查看 Backlog。"}</p></div>}
            </div>
          </section>

          <section className="task-view-card task-control-log">
            <header className="task-view-section-title"><span><MessageSquareText size={15} /> 最近事件</span><small>{detail?.log.length ?? 0} TOTAL</small></header>
            <div>
              {logs.map((entry) => (
                <article key={entry.log_id}>
                  <span className={`log-category category-${entry.category}`}>{entry.category}</span>
                  <div><strong>{entry.summary}</strong><small>{entry.actor_type}:{entry.actor_id} · R{entry.revision}</small></div>
                  <time>{formatTime(entry.occurred_at)}</time>
                </article>
              ))}
              {!logs.length && <div className="task-view-empty"><Clock3 size={27} /><p>当前任务还没有事件。</p></div>}
            </div>
          </section>
        </div>
      </div>
    </section>
  );
}

export function TaskManagerExecution({
  session,
  tasks,
  detail,
  busy,
  error,
  onSelectTask,
  onRefresh,
  onNavigate,
  onAdvanceRun,
  onSubmitHumanDecision,
  onRecordHumanFeedback,
  onAskHumanActionAgent,
  canSubmitHumanDecision,
  isFormalHumanDecision,
}: CommonViewProps) {
  const run = detail?.execution_run;
  const terminalRun = run?.stage === "completed" || run?.stage === "cancelled";
  const coordinator = detail?.coordinator;
  const coordinatorConnected = Boolean(session?.capabilities.serial_coordinator);
  const advanceableActions = new Set([
    "start_node",
    "run_validation",
    "observe_node",
    "verify_node",
    "integrate_checkpoint",
  ]);
  const coordinatorNeedsReconciliation = Boolean(
    coordinator && run && coordinator.run_revision !== run.revision,
  );
  const canAdvance = Boolean(
    session?.capabilities.serial_coordinator
    && run
    && (
      !coordinator
      || coordinatorNeedsReconciliation
      || advanceableActions.has(coordinator.next_action)
    ),
  );
  return (
    <section className="task-view-page task-execution-view page-enter">
      <PageHeader
        eyebrow="RUN / EVIDENCE"
        title="具体任务执行"
        description="逐节点查看 Kernel 状态、Agent 反馈与验证证据。"
      >
        <select
          aria-label="选择需要查看执行情况的任务"
          value={detail?.task.task_id ?? ""}
          disabled={!session || busy || !tasks.length}
          onChange={(event) => onSelectTask(event.target.value)}
        >
          {!tasks.length && <option value="">暂无任务</option>}
          {tasks.map((task) => <option key={task.task_id} value={task.task_id}>{task.title}</option>)}
        </select>
        <button className="button button-quiet" type="button" disabled={!session || busy} onClick={onRefresh}><RefreshCw size={14} /> 刷新</button>
      </PageHeader>
      {error && <div className="task-manager-alert" role="alert"><AlertTriangle size={15} /> {error}</div>}

      {run ? (
        <>
          {terminalRun && (
            <section className={`execution-complete-banner run-${run.stage}`}>
              {run.stage === "completed" ? <CheckCircle2 size={24} /> : <AlertTriangle size={24} />}
              <div>
                <strong>{run.stage === "completed" ? "任务闭环已经完成" : "执行 Run 已取消"}</strong>
                <span>{run.stage === "completed" ? "全部节点满足，最终人工门已通过；当前页面只读保留执行与证据记录。" : "该 Run 已进入终态，不再允许推进；当前页面只读保留已发生的执行与证据记录。"}</span>
              </div>
            </section>
          )}
          <div className="execution-summary">
            <article><span>RUN / STAGE</span><strong>{run.run_id}</strong><small>{run.stage.toUpperCase()} · 计划 R{run.plan_revision} · Run R{run.revision}</small></article>
            <article><span>NODES</span><strong>{run.nodes.length}</strong><small>{run.nodes.filter((node) => satisfiedWorkflowStates.has(node.workflow_state)).length} satisfied</small></article>
            <article><span>UPDATED</span><strong>{formatTime(run.updated_at)}</strong><small>append-only projection</small></article>
          </div>
          {!terminalRun && (
            <section className="task-view-card execution-coordinator">
              <div>
                <span>SERIAL COORDINATOR</span>
                <strong>{coordinator?.stage.toUpperCase() ?? (coordinatorConnected ? "READY" : "NOT CONNECTED")}</strong>
                <small>
                  {coordinator
                    ? `${coordinator.cursor_node_id ?? "run"} · ${coordinator.next_action} · cursor R${coordinator.revision}`
                    : coordinatorConnected
                      ? "执行、验证与 checkpoint 能力已连接；首次推进会从 Run 真相创建持久 cursor。"
                      : "当前启动没有连接串行执行能力；本页不会提供无效推进。"}
                </small>
                <p>{coordinator?.reason ?? (coordinatorConnected
                  ? "点击“推进一步”只执行当前 DAG 的下一项受治理动作，不会跳过 review 或人工门。"
                  : "请关闭当前实例，从仓库根运行 python3 start.py（Windows：py -3 start.py），再返回本页；Run 与证据会保留。")}</p>
              </div>
              <button
                className="button button-primary"
                type="button"
                disabled={!canAdvance || busy}
                onClick={onAdvanceRun}
              >
                {busy ? <LoaderCircle size={14} /> : <ArrowRight size={14} />}
                {coordinatorNeedsReconciliation ? "同步并推进" : "推进一步"}
              </button>
            </section>
          )}
          {detail.human_actions.length > 0 && (
            <div className="execution-human-actions" aria-live="polite">
              {detail.human_actions.map((guidance) => (
                <HumanActionGuidanceCard
                  key={guidance.guidance_id}
                  guidance={guidance}
                  interactive={{
                    busy,
                    canRecordFeedback: Boolean(session?.capabilities.human_action_feedback),
                    canAskAgent: Boolean(session?.capabilities.human_action_agent),
                    interactions: run.nodes.find(
                      (node) => node.node.node_id === guidance.node_id,
                    )?.human_interactions ?? [],
                    canSubmitDecision: canSubmitHumanDecision,
                    isFormalDecision: isFormalHumanDecision,
                    onSubmitDecision: onSubmitHumanDecision,
                    onRecordFeedback: onRecordHumanFeedback,
                    onAskAgent: onAskHumanActionAgent,
                  }}
                />
              ))}
            </div>
          )}
          <section className="task-view-card execution-node-list">
            <header className="task-view-section-title"><span><Activity size={15} /> 节点执行序列</span><small>KERNEL OWNED</small></header>
            <div>
              {run.nodes.map((node, index) => {
                const evidenceCount = new Set([
                  ...(node.provider_reference?.evidence_artifact_ids ?? []),
                  ...(node.latest_observation?.evidence_artifact_ids ?? []),
                  ...(node.verification_evidence?.evidence_artifact_ids ?? []),
                  ...node.transition_history.flatMap((item) => item.evidence_ids),
                  ...node.human_interactions.flatMap((item) => item.evidence_artifact_ids),
                  ...(node.review_artifact_id ? [node.review_artifact_id] : []),
                  ...(node.source_review_artifact_id ? [node.source_review_artifact_id] : []),
                  ...(node.source_approval_artifact_id ? [node.source_approval_artifact_id] : []),
                  ...(node.integration_artifact_id ? [node.integration_artifact_id] : []),
                ]).size;
                return (
                  <article key={node.node.node_id}>
                    <b>{String(index + 1).padStart(2, "0")}</b>
                    <span className="execution-node-state"><i /> {node.workflow_state.toUpperCase()}</span>
                    <div><strong>{node.node.title}</strong><small>{node.latest_observation?.summary ?? (satisfiedWorkflowStates.has(node.workflow_state) ? "节点已由受治理流程满足" : "暂无 Agent 反馈")}</small></div>
                    <span className="execution-node-evidence"><ShieldCheck size={12} /> {evidenceCount} evidence</span>
                    <time>{node.transition_history.length} transitions</time>
                  </article>
                );
              })}
            </div>
          </section>
        </>
      ) : (
        <div className="task-execution-empty task-view-card">
          <Activity size={38} />
          <h2>{detail ? "该任务尚未装配执行 Run" : "选择一个任务"}</h2>
          <p>{detail ? detail.execution_blockers[0] ?? "请先在编排页完成当前唯一的下一步。" : "从总控或这里的任务选择器定位具体任务。"}</p>
          <button type="button" className="button button-primary" onClick={() => onNavigate("orchestration")}><Network size={14} /> 返回任务编排</button>
        </div>
      )}
    </section>
  );
}
