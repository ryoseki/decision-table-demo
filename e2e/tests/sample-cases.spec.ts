import { expect, test, type Page } from "@playwright/test";

// 一覧から共有サンプルを作成し、DBへの保存と詳細表示を確認する。
// 空の一覧から始めるため、他のspecと分けたスキーマのバックエンドへ /api を向ける。
// 再送・保存失敗・同時到達はAPI契約・部品・DB統合テストで確認する。
const EMPTY_BACKEND = "http://e2e-empty-backend:8000";
const CLICKS = 5; // 連続した操作で1件ずつ増えることを確認する

async function useEmptyBackend(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: EMPTY_BACKEND + url.pathname + url.search });
    await route.fulfill({ response });
  });
}

const totalShown = async (page: Page) => {
  const text = await page.getByText(/件を表示（全\d+件）/).textContent();
  return Number(/全(\d+)件/.exec(text ?? "")?.[1]);
};
// 表は overflow: hidden の外枠に入るため、document 幅だけでは内容の見切れを検出できない。
// 表の外枠の内側でも横はみ出しを確認する。
const expectNoHorizontalOverflow = async (page: Page) => {
  const overflow = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth - window.innerWidth,
    tableWraps: [...document.querySelectorAll<HTMLElement>(".table-wrap")].map((w) => w.scrollWidth - w.clientWidth),
  }));
  expect(overflow.document).toBeLessThanOrEqual(0);
  for (const over of overflow.tableWraps) expect(over).toBeLessThanOrEqual(0);
};
const SAMPLE_ID = /^CASE-[0-9A-F]{8}$/;
// 新しい案件IDそのものが13文字。省略せずそのまま表示し、APIとリンクも同じIDを使う。
const caseRows = (page: Page) => page.getByRole("row").filter({ has: page.getByRole("rowheader") });

test("空の一覧からボタンを押すたびに共有サンプル案件が1件ずつ増え、13文字の案件IDで表示され、1件を開ける。デスクトップとモバイル幅で横にはみ出さない", async ({ page, request }) => {
  // 前提: 案件が1件もない（scripts/e2e.sh が実行のたびに専用DBを作り直す）。
  const before = (await (await request.get(`${EMPTY_BACKEND}/api/cases`)).json()) as { case_id: string }[];
  expect(before).toEqual([]);

  await useEmptyBackend(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "審査対象一覧" })).toBeVisible();
  await expect(page.getByTestId("empty-list")).toBeVisible();
  expect(await totalShown(page)).toBe(0);

  // 押すたびに、再読み込みなしで、一覧が1件ずつ増える（別の操作は、別の案件）
  for (let n = 1; n <= CLICKS; n++) {
    await page.getByRole("button", { name: "サンプル案件を作成" }).click();
    await expect(page.getByTestId("sample-notice")).toContainText("1件作成しました");
    await expect(caseRows(page)).toHaveCount(n);
    await expect.poll(() => totalShown(page)).toBe(n);
    // 通知は生成された13文字の案件IDをそのまま表示する
    await expect(page.getByTestId("sample-notice")).toHaveText(/^サンプル案件を1件作成しました（CASE-[0-9A-F]{8}）。$/);
    await expect(page.getByTestId("sample-notice")).toHaveAttribute("title", SAMPLE_ID);
  }

  // API・画面・titleが同じ13文字の案件IDを使い、操作ごとに別の案件になる。
  const stored = (await (await request.get(`${EMPTY_BACKEND}/api/cases`)).json()) as { case_id: string }[];
  const createdIds = stored.map((c) => c.case_id);
  expect(createdIds).toHaveLength(CLICKS);
  expect(new Set(createdIds).size).toBe(CLICKS); // 毎回、別の案件
  expect(createdIds.every((id) => SAMPLE_ID.test(id))).toBe(true);
  const rowHeaders = page.getByRole("rowheader");
  const titles = await rowHeaders.evaluateAll((els) => els.map((e) => e.getAttribute("title")));
  expect([...titles].sort()).toEqual([...createdIds].sort());
  const labels = (await rowHeaders.allTextContents()).sort();
  expect(labels).toEqual([...createdIds].sort());
  for (const label of labels) expect(label).toHaveLength(13);
  await expectNoHorizontalOverflow(page); // デスクトップ幅の一覧

  // リンクの案件IDに対応する詳細を開く。
  const target = createdIds[0];
  await page
    .getByRole("row")
    .filter({ has: page.locator(`a[href="#/cases/${encodeURIComponent(target)}"]`) })
    .getByRole("link", { name: /審査を開く/ })
    .click();
  await expect(page).toHaveURL(new RegExp(`#/cases/${target}$`));
  await expect(page.getByRole("heading", { level: 2 })).toContainText(target); // 見出しの読み上げにも案件IDを含める
  const sub = page.locator(".sub-row");
  await expect(sub).toContainText(target);
  await expectNoHorizontalOverflow(page); // デスクトップ幅の詳細

  // モバイル幅（360px）でも、詳細と一覧が横にはみ出さない
  await page.setViewportSize({ width: 360, height: 800 });
  await expect(sub).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.goto("/#/");
  await expect(page.getByRole("heading", { name: "審査対象一覧" })).toBeVisible();
  await expect(caseRows(page)).toHaveCount(CLICKS);
  await expectNoHorizontalOverflow(page);
  await page.setViewportSize({ width: 1280, height: 800 });

  // 一覧へ戻っても、作成した案件は共有のまま残っている
  await page.goto("/#/");
  await expect(page.getByRole("heading", { name: "審査対象一覧" })).toBeVisible();
  await expect.poll(() => totalShown(page)).toBe(CLICKS);
});
