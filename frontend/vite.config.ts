import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    // E2EのPlaywrightコンテナが compose のサービス名 frontend で接続するために許可する
    allowedHosts: ["frontend"],
  },
});
