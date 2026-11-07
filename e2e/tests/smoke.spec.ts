import { expect, test } from "@playwright/test";

import { backendUrl } from "../playwright.config";

// 環境のスモークテスト。業務E2Eのシナリオ・入力・期待結果は含めない。
// E2E_EXPECTED_HEALTH_STATUS は、意図的に失敗させて終了結果を確かめるための上書き用。
const expectedHealthStatus = process.env.E2E_EXPECTED_HEALTH_STATUS ?? "ok";

test("初期画面が表示される", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "存在しない見出し" })).toBeVisible();
});

test("GET /health が正常を返す", async ({ request }) => {
  const response = await request.get(`${backendUrl}/health`);
  expect(response.status()).toBe(200);
  expect(await response.json()).toEqual({ status: expectedHealthStatus });
});
