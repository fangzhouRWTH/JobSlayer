import {
  Activity,
  Bot,
  House,
  ListTodo,
  Network,
} from "lucide-react";

export type TaskManagerViewId =
  | "home"
  | "agent"
  | "control"
  | "orchestration"
  | "execution";

export const taskManagerViews: ReadonlyArray<{
  id: TaskManagerViewId;
  label: string;
  shortLabel: string;
  icon: typeof House;
}> = [
  { id: "home", label: "首页与产品说明", shortLabel: "首页", icon: House },
  { id: "agent", label: "Codex 与 Agent 状态", shortLabel: "Agent", icon: Bot },
  { id: "control", label: "任务 Backlog 与总控", shortLabel: "总控", icon: ListTodo },
  { id: "orchestration", label: "任务编排", shortLabel: "编排", icon: Network },
  { id: "execution", label: "具体任务执行", shortLabel: "执行", icon: Activity },
];

export function readTaskManagerView(hash: string): TaskManagerViewId {
  const candidate = hash.replace(/^#\/?/, "");
  if (candidate === "task-manager") return "orchestration";
  return taskManagerViews.some((item) => item.id === candidate)
    ? candidate as TaskManagerViewId
    : "home";
}

interface TaskManagerRailProps {
  activeView: TaskManagerViewId;
  connected: boolean;
  attentionCount: number;
  onSelect: (view: TaskManagerViewId) => void;
}

export function TaskManagerRail({
  activeView,
  connected,
  attentionCount,
  onSelect,
}: TaskManagerRailProps) {
  return (
    <nav className="task-view-rail" aria-label="TaskManager 版面">
      <button
        className="task-view-brand"
        type="button"
        aria-label="打开 JobSlayer 首页"
        title="JobSlayer 首页"
        onClick={() => onSelect("home")}
      >
        <span>JS</span>
      </button>

      <div className="task-view-links">
        {taskManagerViews.map((item) => {
          const Icon = item.icon;
          const active = item.id === activeView;
          return (
            <button
              key={item.id}
              type="button"
              className={active ? "active" : ""}
              aria-label={item.label}
              aria-current={active ? "page" : undefined}
              title={item.label}
              onClick={() => onSelect(item.id)}
            >
              <Icon size={19} strokeWidth={1.8} />
              <span>{item.shortLabel}</span>
              {item.id === "control" && attentionCount > 0 && (
                <i aria-label={`${attentionCount} 个任务需要关注`}>
                  {Math.min(attentionCount, 99)}
                </i>
              )}
            </button>
          );
        })}
      </div>

      <div className={`task-view-connection ${connected ? "connected" : ""}`}>
        <span />
        <small>{connected ? "LOCAL" : "OFFLINE"}</small>
      </div>
    </nav>
  );
}
