import { expect, test } from "@playwright/test";

import { allowLocal, expectedSha } from "../playwright.config";

// 配布先のスモーク確認。業務のシナリオは含めない。
// 画面（ビルド済みのバンドルが動くこと）、/health、/release を別のテストとして判定する。

test("初期画面がHTTP 200で、見出しとバンドルが読み込まれる", async ({ page }) => {
  const loaded = new Map<string, { status: number; contentType: string }>();
  page.on("response", (response) => {
    loaded.set(response.url(), {
      status: response.status(),
      contentType: response.headers()["content-type"] ?? "",
    });
  });

  const response = await page.goto("/");
  expect(response?.status()).toBe(200);
  // 見出しはReactが描画するため、バンドルのJSが実際に動いたことの確認になる。
  await expect(page.getByRole("heading", { name: "不正審査デモ" })).toBeVisible();

  const scripts = await page
    .locator("script[src]")
    .evaluateAll((nodes) => nodes.map((node) => (node as HTMLScriptElement).src));
  const styles = await page
    .locator('link[rel="stylesheet"]')
    .evaluateAll((nodes) => nodes.map((node) => (node as HTMLLinkElement).href));

  expect(scripts.length, "HTMLがバンドルのJSを参照していること").toBeGreaterThan(0);
  for (const url of scripts) {
    expect(new URL(url).pathname, url).toMatch(/\.js$/);
    expect(loaded.get(url)?.status, `JS: ${url}`).toBe(200);
    expect(loaded.get(url)?.contentType, `JS: ${url}`).toMatch(/javascript/);
  }
  // 現在の画面にはCSSがない。参照があれば、そのすべてが読み込まれていること。
  for (const url of styles) {
    expect(new URL(url).pathname, url).toMatch(/\.css$/);
    expect(loaded.get(url)?.status, `CSS: ${url}`).toBe(200);
    expect(loaded.get(url)?.contentType, `CSS: ${url}`).toMatch(/css/);
  }
});

test("GET /health がHTTP 200で本文が完全一致する", async ({ request }) => {
  const response = await request.get("/health");
  expect(response.status()).toBe(200);
  expect(await response.json()).toStrictEqual({ status: "ok" });
});

test("GET /release が期待するSHAと配布可否を返す", async ({ request }) => {
  const response = await request.get("/release");
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(Object.keys(body).sort()).toStrictEqual(["releaseEligible", "sourceSHA"]);
  expect(body.sourceSHA).toBe(expectedSha);
  if (allowLocal) {
    // ローカル統合確認では、dirtyな成果物（false）も許す。
    expect(typeof body.releaseEligible).toBe("boolean");
  } else {
    expect(body.releaseEligible).toBe(true);
  }
});

test("存在しないアセットは404で、画面にフォールバックしない", async ({ request }) => {
  const response = await request.get("/assets/does-not-exist.js");
  expect(response.status()).toBe(404);
});
