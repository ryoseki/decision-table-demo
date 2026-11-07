import { defineConfig } from "@playwright/test";

// 起動と待機は compose（healthcheck付き）が担当する。
// ここではアプリのURLだけを環境変数で受け取る。
export const frontendUrl = process.env.E2E_FRONTEND_URL ?? "http://frontend:5173";
export const backendUrl = process.env.E2E_BACKEND_URL ?? "http://backend:8000";

export default defineConfig({
  testDir: "./tests",
  forbidOnly: !!process.env.CI,
  workers: 1,
  retries: 0,
  // CIでは必ず全体の上限を設ける（Playwright公式のCI手順）
  globalTimeout: 10 * 60 * 1000,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: frontendUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
