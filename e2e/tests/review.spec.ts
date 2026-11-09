import { expect, test, type Page } from "@playwright/test";

// 個別審査の画面案（保存なし）の操作確認。受入例1〜3と、確認待ち・結果選択の制限なしを確かめる。
// SHOTS=1 のとき、目視確認用のスクリーンショットを test-results/shots に出す。
const shot = async (page: Page, name: string) => {
  if (process.env.SHOTS) await page.screenshot({ path: `test-results/shots/${name}.png`, fullPage: true });
};
const answer = (page: Page, q: string, v: "Yes" | "No") =>
  page.getByTestId(`q-${q}`).getByRole("button", { name: v, exact: true }).click();
const display = (page: Page, r: string) => page.getByTestId(`rule-${r}`).getAttribute("data-display");
const isOpen = (page: Page, r: string) => page.getByTestId(`rule-${r}`).evaluate((e) => (e as HTMLDetailsElement).open);

test.beforeEach(async ({ page }) => {
  await page.goto("/#/cases/CASE-B");
  await expect(page.getByTestId("preview-note")).toContainText("保存はまだ行いません");
});

test("例1: A01=Yes・A03=Yes でR01・R02は通常、R03はグレー。一致条件が緑", async ({ page }) => {
  await answer(page, "A01", "Yes");
  await answer(page, "A03", "Yes");
  expect([await display(page, "R01"), await display(page, "R02"), await display(page, "R03")]).toEqual(["normal", "normal", "gray"]);
  expect([await isOpen(page, "R01"), await isOpen(page, "R02"), await isOpen(page, "R03")]).toEqual([true, true, false]);
  await expect(page.getByTestId("cond-R01-A01")).toHaveClass(/matched/);
  await expect(page.getByTestId("cond-R01-A03")).toHaveClass(/matched/);
  await expect(page.getByTestId("cond-R01-A02")).not.toHaveClass(/matched/); // 未回答は緑にしない
  await expect(page.getByTestId("rule-count")).toHaveText("照合 2／3件");
  await expect(page.getByTestId("rule-R03")).not.toContainText("不一致");
  await shot(page, "ex1");
});

test("例2: A01をNoに訂正するとR01・R02はグレー、R03は通常で開く", async ({ page }) => {
  await answer(page, "A01", "Yes");
  await answer(page, "A03", "Yes");
  await answer(page, "A01", "No");
  expect([await display(page, "R01"), await display(page, "R02"), await display(page, "R03")]).toEqual(["gray", "gray", "normal"]);
  expect([await isOpen(page, "R01"), await isOpen(page, "R02"), await isOpen(page, "R03")]).toEqual([false, false, true]);
  await shot(page, "ex2");
});

test("手動の開閉は回答が変わるまで維持される", async ({ page }) => {
  await answer(page, "A01", "Yes");
  await page.getByTestId("rule-R03").locator("summary").click();
  expect(await isOpen(page, "R03")).toBe(true);
  await answer(page, "A01", "Yes"); // 値が変わらない操作では開閉を更新しない
  expect(await isOpen(page, "R03")).toBe(true);
  await answer(page, "A03", "Yes"); // 値が変わると規則どおりに更新する（R03はグレーなので閉じる）
  expect(await isOpen(page, "R03")).toBe(false);
});

test("例3: 全行グレーでも結果「問題なし」を選んで［判定する］で画面例を表示できる", async ({ page }) => {
  await answer(page, "A01", "Yes");
  await answer(page, "A03", "No");
  expect([await display(page, "R01"), await display(page, "R02"), await display(page, "R03")]).toEqual(["gray", "gray", "gray"]);
  await expect(page.getByTestId("rule-count")).toHaveText("照合 0／3件");
  for (const r of ["アカウント削除", "アカウント一時凍結", "注意", "問題なし"]) {
    await expect(page.getByRole("radio", { name: r })).toBeEnabled();
  }
  await page.getByRole("radio", { name: "問題なし" }).check();
  await page.getByRole("button", { name: "判定する" }).click();
  await expect(page.getByTestId("registered")).toHaveText("登録結果（画面例） 問題なし");
  await shot(page, "ex3");
});

test("未回答のまま結果を選んで判定できる。保存・通信・ストレージは使わない", async ({ page }) => {
  const requests: string[] = [];
  page.on("request", (r) => requests.push(r.url()));
  await page.getByRole("radio", { name: "アカウント削除" }).check();
  await page.getByRole("button", { name: "判定する" }).click();
  await expect(page.getByTestId("registered")).toHaveText("登録結果（画面例） アカウント削除");
  expect(requests).toEqual([]);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  await page.reload();
  await expect(page.getByTestId("registered")).toHaveCount(0); // 開き直すと初期状態
});

test("確認待ちは食い違いにせず、Noで解消できる", async ({ page }) => {
  await page.goto("/#/cases/CASE-D");
  const q = page.getByTestId("q-A09");
  await expect(q).toContainText("確認待ち");
  await expect(q.getByRole("button", { name: "Yes", exact: true })).toHaveAttribute("aria-pressed", "false");
  await expect(q.getByRole("button", { name: "No", exact: true })).toHaveAttribute("aria-pressed", "false");
  await shot(page, "pending");
  await answer(page, "A09", "No");
  await expect(q).not.toContainText("確認待ち");
  await expect(q.getByRole("button", { name: "No", exact: true })).toHaveAttribute("aria-pressed", "true");
});

test("モバイル幅で横にはみ出さない", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await answer(page, "A01", "Yes");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await shot(page, "mobile");
});
