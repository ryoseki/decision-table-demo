import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    // E2EのPlaywrightコンテナが compose のサービス名 frontend で接続するために許可する
    allowedHosts: ["frontend"],
  },
  // 単体テスト（npm test）。ブラウザなしでDOMを使うため jsdom で動かす。
  // サーバーには接続しない（APIはテスト側でモックする）。
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
