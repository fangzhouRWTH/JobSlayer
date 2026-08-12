import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // ECharts is already a route-lazy, tree-shaken chunk. Keep the warning
    // meaningful without treating this isolated visualization module as a failure.
    chunkSizeWarningLimit: 600,
  },
  server: {
    host: "127.0.0.1",
    port: 4173,
  },
  preview: {
    host: "127.0.0.1",
    port: 4174,
  },
});
