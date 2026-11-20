import { expect, test, type Page } from "@playwright/test";

// 共有のサンプル案件（架空の固定4件）を、一覧のボタンから作る流れ。DBまで通す。
// 受入条件は「空の一覧から」。ほかのspecが案件を作る共有DBでは空にならないため、案件が1件もない
// 専用スキーマと、それを使う専用バックエンド（compose.e2e.yaml の e2e-empty-backend）へ、
// 画面の /api を向ける（shared-demo と同じ方法）。上の層で確認している細かい挙動は、ここでは繰り返さない。
const EMPTY_BACKEND = "http://e2e-empty-backend:8000";
const SAMPLE_IDS = ["CASE-SAMPLE-01", "CASE-SAMPLE-02", "CASE-SAMPLE-03", "CASE-SAMPLE-04"];

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
const rowOf = (page: Page, caseId: string) => page.getByRole("row").filter({ has: page.getByRole("rowheader", { name: caseId }) });

test("空の一覧からボタンで共有サンプル4件を作り、1件を開ける。再実行で総数は増えない", async ({ page, request }) => {
  // 前提: 案件が1件もない（専用スキーマ。E2Eの専用DBごと、実行のたびに作り直す）。
  const before = (await (await request.get(`${EMPTY_BACKEND}/api/cases`)).json()) as { case_id: string }[];
  expect(before).toEqual([]);

  await useEmptyBackend(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "審査対象一覧" })).toBeVisible();
  await expect(page.getByTestId("empty-list")).toBeVisible();
  const total0 = await totalShown(page);
  expect(total0).toBe(0);

  // 作成（再読み込みなしで、一覧に4件が出る）
  await page.getByRole("button", { name: "サンプル案件を作成" }).click();
  await expect(page.getByTestId("sample-notice")).toContainText("4件作成しました");
  for (const id of SAMPLE_IDS) await expect(rowOf(page, id)).toHaveCount(1);
  expect(await totalShown(page)).toBe(total0 + 4);
  await expect(rowOf(page, "CASE-SAMPLE-01").getByText("未審査")).toBeVisible();
  await expect(rowOf(page, "CASE-SAMPLE-02").getByText("未審査")).toBeVisible();
  await expect(rowOf(page, "CASE-SAMPLE-03").getByText("取得エラー")).toBeVisible();
  await expect(rowOf(page, "CASE-SAMPLE-04").getByText("登録済み")).toBeVisible();

  // 1件を開く（登録済みの案件は、閲覧のみ）
  await rowOf(page, "CASE-SAMPLE-04").getByRole("link", { name: /審査を開く/ }).click();
  await expect(page.getByRole("heading", { level: 2 })).toContainText("CASE-SAMPLE-04");
  await expect(page.getByText("登録済みの結果: 問題なし")).toBeVisible();
  await expect(page.getByTestId("readonly-note")).toBeVisible();

  // 確認待ちの案件（A09の取得エラー）も開ける
  await page.goto("/#/cases/CASE-SAMPLE-03");
  await expect(page.getByRole("heading", { level: 2 })).toContainText("CASE-SAMPLE-03");
  await expect(page.getByRole("button", { name: "判定する" })).toBeVisible();

  // 再実行: 作成済みと知らせ、総数は増えない（画面でも、APIでも）
  await page.goto("/#/");
  await expect(page.getByRole("heading", { name: "審査対象一覧" })).toBeVisible();
  await expect.poll(() => totalShown(page)).toBe(total0 + 4);
  await page.getByRole("button", { name: "サンプル案件を作成" }).click();
  await expect(page.getByTestId("sample-notice")).toContainText("作成済み");
  await expect.poll(() => totalShown(page)).toBe(total0 + 4);
  for (const id of SAMPLE_IDS) await expect(rowOf(page, id)).toHaveCount(1);

  const again = await request.post(`${EMPTY_BACKEND}/api/sample-cases`, { data: {} });
  expect(again.status()).toBe(200);
  expect((await again.json()).created).toEqual([]);
  const after = (await (await request.get(`${EMPTY_BACKEND}/api/cases`)).json()) as { case_id: string }[];
  expect(after).toHaveLength(total0 + 4);
  expect(after.filter((c) => SAMPLE_IDS.includes(c.case_id))).toHaveLength(4);

  // 登録済みのサンプルの判定は、再実行でも変わらない。
  const registered = await (await request.get(`${EMPTY_BACKEND}/api/cases/CASE-SAMPLE-04`)).json();
  expect(registered.result.result_code).toBe("no_issue");
});
