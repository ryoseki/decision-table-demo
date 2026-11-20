import { expect, test, type Page } from "@playwright/test";

// 共有のサンプル案件を、一覧のボタンから1回の操作につき1件作る流れ。DBまで通す。
// 受入条件は「空の一覧から」。ほかのspecが案件を作る共有DBでは空にならないため、案件が1件もない
// 専用スキーマと、それを使う専用バックエンド（compose.e2e.yaml の e2e-empty-backend）へ、
// 画面の /api を向ける（shared-demo と同じ方法）。上の層で確認している細かい挙動（再送・失敗後の再試行・
// 同時の到達など）は、ここでは繰り返さない。
const EMPTY_BACKEND = "http://e2e-empty-backend:8000";
const CLICKS = 5; // 4回を超えても、1回ごとに1件ずつ増える

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
const caseRows = (page: Page) => page.getByRole("row").filter({ has: page.getByRole("rowheader") });

test("空の一覧からボタンを押すたびに共有サンプル案件が1件ずつ増え（4件を超えても）、1件を開ける", async ({ page, request }) => {
  // 前提: 案件が1件もない（専用スキーマ。E2Eの専用DBごと、実行のたびに作り直す）。
  const before = (await (await request.get(`${EMPTY_BACKEND}/api/cases`)).json()) as { case_id: string }[];
  expect(before).toEqual([]);

  await useEmptyBackend(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "審査対象一覧" })).toBeVisible();
  await expect(page.getByTestId("empty-list")).toBeVisible();
  expect(await totalShown(page)).toBe(0);

  // 押すたびに、再読み込みなしで、一覧が1件ずつ増える（別の操作は、別の案件）
  const createdIds: string[] = [];
  for (let n = 1; n <= CLICKS; n++) {
    await page.getByRole("button", { name: "サンプル案件を作成" }).click();
    await expect(page.getByTestId("sample-notice")).toContainText("1件作成しました");
    await expect(caseRows(page)).toHaveCount(n);
    await expect.poll(() => totalShown(page)).toBe(n);
    const notice = (await page.getByTestId("sample-notice").textContent()) ?? "";
    createdIds.push(/CASE-SAMPLE-[0-9A-F]{32}/.exec(notice)?.[0] ?? "");
  }
  expect(createdIds.every((id) => id !== "")).toBe(true);
  expect(new Set(createdIds).size).toBe(CLICKS); // 毎回、別の案件

  // APIでも、共有の案件が、押した回数だけある
  const stored = (await (await request.get(`${EMPTY_BACKEND}/api/cases`)).json()) as { case_id: string }[];
  expect(stored.map((c) => c.case_id).sort()).toEqual([...createdIds].sort());

  // 1件を開く（開いた案件は、詳細に案件IDが出る）。作成した案件の型（未審査・確認待ち・登録済み）は問わない。
  const target = createdIds[0];
  await page
    .getByRole("row")
    .filter({ has: page.getByRole("rowheader", { name: target }) })
    .getByRole("link", { name: /審査を開く/ })
    .click();
  await expect(page.getByRole("heading", { level: 2 })).toContainText(target);

  // 一覧へ戻っても、作成した案件は共有のまま残っている
  await page.goto("/#/");
  await expect(page.getByRole("heading", { name: "審査対象一覧" })).toBeVisible();
  await expect.poll(() => totalShown(page)).toBe(CLICKS);
});
