import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import {
  Bell,
  Command,
  HelpCircle,
  Home,
  Menu,
  Network,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { CommandPalette } from "./components/CommandPalette";
import { Overview } from "./components/Overview";
import type { ViewId } from "./types";

const WorkflowStudio = lazy(() => import("./components/WorkflowStudio").then((module) => ({ default: module.WorkflowStudio })));
const TaskManager = lazy(() => import("./components/TaskManager").then((module) => ({ default: module.TaskManager })));
const TaskOrchestration = lazy(() => import("./components/TaskOrchestration").then((module) => ({ default: module.TaskOrchestration })));
const RunInspector = lazy(() => import("./components/RunInspector").then((module) => ({ default: module.RunInspector })));
const ArtifactReview = lazy(() => import("./components/ArtifactReview").then((module) => ({ default: module.ArtifactReview })));
const Observability = lazy(() => import("./components/Observability").then((module) => ({ default: module.Observability })));

const navItems: Array<{ id: ViewId; label: string; icon: typeof Home }> = [
  { id: "task-manager", label: "TaskManager", icon: Network },
];

const availableViews: ViewId[] = ["task-manager", "overview", "orchestration", "workflow", "run", "artifact", "observability"];

const viewLabels: Record<ViewId, string> = {
  "task-manager": "TaskManager",
  overview: "Interaction Prototype",
  orchestration: "Task Orchestration",
  workflow: "Workflow Studio",
  run: "Run Inspector",
  artifact: "Artifact Review",
  observability: "Observability",
};

export default function App() {
  const [view, setView] = useState<ViewId>(() => {
    const hash = window.location.hash.replace("#/", "") as ViewId;
    return availableViews.includes(hash) ? hash : "task-manager";
  });
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const navigate = (next: ViewId) => {
    setView(next);
    window.location.hash = `/${next}`;
    setMobileNav(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault(); setPaletteOpen((open) => !open);
      }
      if (event.key === "Escape") { setPaletteOpen(false); setMobileNav(false); }
    };
    const onHash = () => {
      const hash = window.location.hash.replace("#/", "") as ViewId;
      if (availableViews.includes(hash)) setView(hash);
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("hashchange", onHash);
    return () => { window.removeEventListener("keydown", onKeyDown); window.removeEventListener("hashchange", onHash); };
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 4200);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const content = useMemo(() => {
    if (view === "task-manager") return <TaskManager onNotice={setNotice} />;
    if (view === "orchestration") return <TaskOrchestration onNotice={setNotice} />;
    if (view === "workflow") return <WorkflowStudio onNotice={setNotice} />;
    if (view === "run") return <RunInspector onNotice={setNotice} />;
    if (view === "artifact") return <ArtifactReview onNotice={setNotice} />;
    if (view === "observability") return <Observability />;
    return <Overview onNavigate={navigate} />;
  }, [view]);

  return (
    <div className="app-shell">
      <header className="global-header">
        <button className="mobile-menu" aria-label="打开导航" onClick={() => setMobileNav(true)}><Menu size={19} /></button>
        <button className="brand" onClick={() => navigate("task-manager")} aria-label="回到 TaskManager">
          <span className="brand-mark">JS</span>
          <span><strong>JOBSLAYER</strong><small>ENGINEERING WORKBENCH</small></span>
        </button>
        <div className="breadcrumb"><span>BraveNewWorld</span><i>/</i><strong>{viewLabels[view]}</strong></div>
        <button className="global-search" onClick={() => setPaletteOpen(true)}><Search size={15} /><span>Search tasks, backlog, logs…</span><kbd><Command size={11} /> K</kbd></button>
        <div className="header-actions">
          <span className="mode-badge"><span /> {view === "task-manager" || view === "orchestration" ? "LOCAL API · VERSIONED" : "LEGACY LAB · MOCK"}</span>
          <button className="icon-button" aria-label="通知示例"><Bell size={16} /><i>3</i></button>
          <button className="avatar-button" aria-label="演示用户">FR</button>
        </div>
      </header>

      <aside className={`global-sidebar ${mobileNav ? "mobile-open" : ""}`}>
        <div className="mobile-sidebar-head"><span>NAVIGATION</span><button aria-label="关闭导航" onClick={() => setMobileNav(false)}><X size={18} /></button></div>
        <nav aria-label="原型页面">
          <span className="nav-label">PRIMARY APPLICATION</span>
          {navItems.map((item) => { const Icon = item.icon; return (
            <button key={item.id} className={view === item.id ? "active" : ""} onClick={() => navigate(item.id)} aria-current={view === item.id ? "page" : undefined}>
              <Icon size={17} /><span>{item.label}</span><i className="nav-live" />
            </button>
          ); })}
        </nav>
        <div className="sidebar-separator" />
        <div className="sidebar-context">
          <span className="nav-label">AUTHORITY BOUNDARY</span>
          <div><ShieldCheck size={16} /><span><strong>{view === "task-manager" ? "Task truth governed" : view === "orchestration" ? "Plan API governed" : "Legacy lab isolated"}</strong><small>{view === "task-manager" || view === "orchestration" ? "Agent proposes · user applies" : "No API connected"}</small></span></div>
          <div className="boundary-meter"><i /></div>
          <p>{view === "task-manager" || view === "orchestration" ? <>Plan revisions are append-only.<br />Execution is capability-gated.</> : <>Legacy routes remain direct-only.<br />They do not own truth.</>}</p>
        </div>
        <div className="sidebar-bottom"><button><HelpCircle size={16} /><span>Design guide</span></button><span>UI / 0.1.0</span></div>
      </aside>
      {mobileNav && <button className="mobile-scrim" aria-label="关闭导航遮罩" onClick={() => setMobileNav(false)} />}

      <main className="app-content">
        <Suspense fallback={<div className="view-loading"><span /><strong>Loading workbench module</strong><small>UI module only · no control-plane command</small></div>}>
          {content}
        </Suspense>
      </main>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} onNavigate={navigate} />
      {notice && <div className="toast" role="status"><ShieldCheck size={17} /><span>{notice}</span><button aria-label="关闭提示" onClick={() => setNotice(null)}><X size={15} /></button></div>}
    </div>
  );
}
