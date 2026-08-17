import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import {
  Activity,
  BarChart3,
  Bell,
  Command,
  FileSearch,
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
const RunInspector = lazy(() => import("./components/RunInspector").then((module) => ({ default: module.RunInspector })));
const ArtifactReview = lazy(() => import("./components/ArtifactReview").then((module) => ({ default: module.ArtifactReview })));
const Observability = lazy(() => import("./components/Observability").then((module) => ({ default: module.Observability })));

const navItems: Array<{ id: ViewId; label: string; icon: typeof Home }> = [
  { id: "overview", label: "Prototype index", icon: Home },
  { id: "workflow", label: "Workflow Studio", icon: Network },
  { id: "run", label: "Run Inspector", icon: Activity },
  { id: "artifact", label: "Artifact Review", icon: FileSearch },
  { id: "observability", label: "Observability", icon: BarChart3 },
];

const viewLabels: Record<ViewId, string> = {
  overview: "Interaction Prototype",
  workflow: "Workflow Studio",
  run: "Run Inspector",
  artifact: "Artifact Review",
  observability: "Observability",
};

export default function App() {
  const [view, setView] = useState<ViewId>(() => {
    const hash = window.location.hash.replace("#/", "") as ViewId;
    return navItems.some((item) => item.id === hash) ? hash : "overview";
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
      if (navItems.some((item) => item.id === hash)) setView(hash);
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
        <button className="brand" onClick={() => navigate("overview")} aria-label="回到原型目录">
          <span className="brand-mark">JS</span>
          <span><strong>JOBSLAYER</strong><small>ENGINEERING WORKBENCH</small></span>
        </button>
        <div className="breadcrumb"><span>BraveNewWorld</span><i>/</i><strong>{viewLabels[view]}</strong></div>
        <button className="global-search" onClick={() => setPaletteOpen(true)}><Search size={15} /><span>Search runs, tasks, artifacts…</span><kbd><Command size={11} /> K</kbd></button>
        <div className="header-actions">
          <span className="mode-badge"><span /> PROTOTYPE · MOCK</span>
          <button className="icon-button" aria-label="通知示例"><Bell size={16} /><i>3</i></button>
          <button className="avatar-button" aria-label="演示用户">FR</button>
        </div>
      </header>

      <aside className={`global-sidebar ${mobileNav ? "mobile-open" : ""}`}>
        <div className="mobile-sidebar-head"><span>NAVIGATION</span><button aria-label="关闭导航" onClick={() => setMobileNav(false)}><X size={18} /></button></div>
        <nav aria-label="原型页面">
          <span className="nav-label">WORKBENCH</span>
          {navItems.map((item) => { const Icon = item.icon; return (
            <button key={item.id} className={view === item.id ? "active" : ""} onClick={() => navigate(item.id)} aria-current={view === item.id ? "page" : undefined}>
              <Icon size={17} /><span>{item.label}</span>{item.id === "run" && <i className="nav-live" />}
            </button>
          ); })}
        </nav>
        <div className="sidebar-separator" />
        <div className="sidebar-context">
          <span className="nav-label">PROTOTYPE BOUNDARY</span>
          <div><ShieldCheck size={16} /><span><strong>Kernel isolated</strong><small>No API connected</small></span></div>
          <div className="boundary-meter"><i /></div>
          <p>UI state is disposable.<br />Engineering truth is not.</p>
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
