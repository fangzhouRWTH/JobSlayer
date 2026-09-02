import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, ".", "");
  const apiPort = Number.parseInt(environment.JOBSLAYER_API_PORT ?? "8780", 10);

  if (!Number.isInteger(apiPort) || apiPort < 1 || apiPort > 65535) {
    throw new Error("JOBSLAYER_API_PORT must be an integer between 1 and 65535");
  }

  return {
    plugins: [react()],
    build: {
      // ECharts is already a route-lazy, tree-shaken chunk. Keep the warning
      // meaningful without treating this isolated visualization module as a failure.
      chunkSizeWarningLimit: 600,
    },
    server: {
      host: "127.0.0.1",
      port: 4173,
      proxy: {
        "/api/task-manager": {
          target: `http://127.0.0.1:${apiPort}`,
        },
        "/api/orchestration": {
          target: `http://127.0.0.1:${apiPort}`,
        },
      },
    },
    preview: {
      host: "127.0.0.1",
      port: 4174,
    },
  };
});
