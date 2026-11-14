import { expect, test } from "@playwright/test";

import { backendUrl } from "../playwright.config";

// 設問管理（デモ）の業務E2E。設問の追加案を作る → 差分を確認 → No で現行データが変わらない →
// もう一度作り、Yes で反映される → 再読み込みしても残る。AIはモック、保存はバックエンドのメモリ（DBなし）。
// 細かい挙動（エラー表示、競合、再利用の拒否など）は、画面の部品テスト（frontend/src/QuestionAdmin.test.tsx）と
// API契約テスト（backend/tests/test_question_admin.py）で確認する。
const PROMPT = "APIキーの失効履歴を確認する設問を追加して";

test("設問の追加案を作る → 差分を確認 → No で変わらない → もう一度作り Yes で反映（再読み込みでも残る）", async ({ page, request }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "設問管理" }).click();
  await expect(page.getByRole("heading", { name: "設問管理（デモ）" })).toBeVisible();
  await expect(page.getByTestId("mock-note")).toContainText("モックです");
  const current = page.getByRole("region", { name: "現行のデモの設問一覧" });
  await expect(current.getByRole("row")).toHaveCount(13); // 見出し + A01〜A12

  const propose = async () => {
    await page.getByLabel("変更の依頼（自然文）").fill(PROMPT);
    await page.getByRole("button", { name: "追加案を作る" }).click();
    await expect(page.getByTestId("proposal")).toBeVisible();
  };

  // 1回目: 差分を確認し、No。
  await propose();
  const before = page.getByRole("region", { name: "Before（現行）" });
  const after = page.getByRole("region", { name: "After（反映した場合）" });
  await expect(before.getByRole("row")).toHaveCount(13);
  await expect(after.getByRole("row")).toHaveCount(14);
  const added = after.getByTestId("After（反映した場合）-A13");
  await expect(added).toContainText("追加");
  await expect(added).toHaveClass(/row-added/);
  await expect(page.getByText("反映しますか？")).toBeVisible();
  await page.getByRole("button", { name: "No" }).click();
  await expect(page.getByTestId("proposal")).toHaveCount(0);
  await expect(current.getByRole("row")).toHaveCount(13);
  await page.reload(); // 再読み込みしても、変わっていない
  await expect(current.getByRole("row")).toHaveCount(13);
  let state = await (await request.get(`${backendUrl}/api/admin/questions`)).json();
  expect(state.questions).toHaveLength(12);

  // 2回目: Yes で反映される。
  await propose();
  await page.getByRole("button", { name: "Yes" }).click();
  await expect(page.getByTestId("proposal-notice")).toHaveText("デモの設問一覧に反映しました");
  await expect(page.getByTestId("proposal")).toHaveCount(0);
  await expect(current.getByRole("row")).toHaveCount(14);
  await expect(current.getByTestId("現行のデモの設問一覧-A13")).toBeVisible();

  // 再読み込みしても残る（バックエンドのメモリ。DBではない）。
  await page.reload();
  await expect(current.getByTestId("現行のデモの設問一覧-A13")).toBeVisible();
  state = await (await request.get(`${backendUrl}/api/admin/questions`)).json();
  expect(state.questions.map((q: { id: string }) => q.id).at(-1)).toBe("A13");
  expect(state.persistence).toBe("memory");
});
