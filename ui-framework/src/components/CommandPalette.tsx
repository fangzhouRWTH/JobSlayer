import { useEffect, useMemo, useState } from "react";
import { Activity, BarChart3, FileSearch, Home, Network, Search, X } from "lucide-react";
import type { ViewId } from "../types";

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  onNavigate: (view: ViewId) => void;
}

const commands: Array<{ id: ViewId; label: string; hint: string; icon: typeof Home }> = [
  { id: "overview", label: "打开原型目录", hint: "Overview", icon: Home },
  { id: "workflow", label: "打开 Workflow Studio", hint: "Author", icon: Network },
  { id: "run", label: "检查 run_ui_028", hint: "Observe", icon: Activity },
  { id: "artifact", label: "审查 implementation-report.md", hint: "Review", icon: FileSearch },
  { id: "observability", label: "打开可观测性示例", hint: "Learn", icon: BarChart3 },
];

export function CommandPalette({ open, onClose, onNavigate }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  useEffect(() => { if (open) setQuery(""); }, [open]);
  const results = useMemo(() => commands.filter((item) => `${item.label} ${item.hint}`.toLowerCase().includes(query.toLowerCase())), [query]);
  if (!open) return null;

  return (
    <div className="palette-backdrop" role="presentation" onMouseDown={onClose}>
      <div className="command-palette" role="dialog" aria-modal="true" aria-label="命令面板" onMouseDown={(event) => event.stopPropagation()}>
        <div className="palette-input"><Search size={18} /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="跳转到界面或查找对象…" /><button aria-label="关闭命令面板" onClick={onClose}><X size={17} /></button></div>
        <div className="palette-label">NAVIGATE</div>
        <div className="palette-results">
          {results.map((item) => { const Icon = item.icon; return <button key={item.id} onClick={() => { onNavigate(item.id); onClose(); }}><Icon size={17} /><span>{item.label}</span><small>{item.hint}</small></button>; })}
          {!results.length && <p>没有匹配的示范页面</p>}
        </div>
        <div className="palette-footer"><span><kbd>↑↓</kbd> 浏览</span><span><kbd>Enter</kbd> 打开</span><span><kbd>Esc</kbd> 关闭</span></div>
      </div>
    </div>
  );
}
