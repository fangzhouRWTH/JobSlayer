import { useEffect, useState } from "react";
import { ShieldCheck, X } from "lucide-react";
import { TaskManager } from "./components/TaskManager";

export default function App() {
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 4200);
    return () => window.clearTimeout(timer);
  }, [notice]);

  return (
    <main className="task-manager-app-shell">
      <TaskManager onNotice={setNotice} />
      {notice && (
        <div className="toast" role="status">
          <ShieldCheck size={17} />
          <span>{notice}</span>
          <button aria-label="关闭提示" onClick={() => setNotice(null)}>
            <X size={15} />
          </button>
        </div>
      )}
    </main>
  );
}
