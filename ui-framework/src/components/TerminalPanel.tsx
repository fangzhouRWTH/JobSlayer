import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";

const lines = [
  "\u001b[90m$\u001b[0m jobslayer check",
  "\u001b[32m✓\u001b[0m domain contracts .......... stable",
  "\u001b[32m✓\u001b[0m workflow audit chain ...... verified",
  "\u001b[32m✓\u001b[0m validation profile ........ 43 / 43",
  "\u001b[33m○\u001b[0m approval gate ............. waiting",
  "",
  "\u001b[90mRead-only artifact · raw output retained separately\u001b[0m",
];

export function TerminalPanel() {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!host.current) return;
    const terminal = new Terminal({
      cols: 92,
      rows: 10,
      disableStdin: true,
      convertEol: true,
      cursorBlink: false,
      fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace',
      fontSize: 12,
      lineHeight: 1.45,
      theme: { background: "#0b0d10", foreground: "#d8dce3", green: "#b9ff66", yellow: "#f2c86b", brightBlack: "#737a86" },
    });
    terminal.open(host.current);
    lines.forEach((line) => terminal.writeln(line));
    return () => terminal.dispose();
  }, []);

  return <div className="terminal-host" ref={host} aria-label="只读终端输出示例" />;
}
