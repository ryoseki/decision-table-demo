import { expect, test } from "@playwright/test";

import { backendUrl } from "../playwright.config";

// 判定ルール管理（デモ）の業務E2E（1シナリオで、異常と正常を続けて確認する）。
//   1) 存在しない設問を参照するルールの案 → 検証エラーになり、現行のルール一覧が変わらない
//   2) 正しい追加案 → 差分を確認 → No で変わらない
//   3) もう一度作り → Yes で反映される → 再読み込みしても残る。審査画面のルール（GET /api/rules）は変わらない
// 細かい挙動（重複、競合、参照する設問の廃止、削除案など）は、画面の部品テスト（frontend/src/RuleAdmin.test.tsx）と
// API契約テスト（backend/tests/test_rule_admin.py）で確認する。AIはモック、保存はバックエンドのメモリ（DBなし）。
// 前提: 実行開始時のデモのルール一覧は初期状態（R01〜R03。次の追加はR04）。playwright.config.ts は retries: 0 で、
// 失敗しても同じ起動中に再試行しない（再試行すると、反映済みのR04が残り、初期状態の前提が崩れる）。
// 再実行は、バックエンドを再起動（compose の作り直し）してから行う。retries を設定する場合は、この前提を見直す。
const UNKNOWN_PROMPT = "設問A99がYesのとき注意とするルールを追加して";
const PROMPT = "設問A09がYes、設問A10がNoのとき注意とするルールを追加して";

type ApiRule = { rule_id: string; conditions: Record<string, string>; result_code: string };

test("判定ルールの追加案: 存在しない設問は検証エラーで変わらない → 正しい案は差分を確認し、No で変わらず、Yes で反映される", async ({
  page,
  request,
}) => {
  const demoRules = async () =>
    (await (await request.get(`${backendUrl}/api/admin/rules`)).json()) as { revision: number; rules: ApiRule[]; persistence: string };
  const reviewRuleIds = async () =>
    ((await (await request.get(`${backendUrl}/api/rules`)).json()) as { rule_id: string }[]).map((r) => r.rule_id);
  const initialReview = await reviewRuleIds();

  await page.goto("/");
  await page.getByRole("link", { name: "判定ルール管理" }).click();
  await expect(page.getByRole("heading", { name: "判定ルール管理（デモ）" })).toBeVisible();
  await expect(page.getByTestId("target")).toHaveText("操作の対象: 判定ルール"); // 設問ではなく、判定ルールを操作する画面
  await expect(page.getByTestId("mock-note")).toContainText("モックです");
  const current = page.getByRole("region", { name: "現行のデモの判定ルール一覧" });
  await expect(current.getByRole("row")).toHaveCount(4); // 見出し + R01〜R03
  const initial = await demoRules();
  expect(initial.rules.map((r) => r.rule_id)).toEqual(["R01", "R02", "R03"]);

  const propose = async (prompt: string) => {
    await page.getByLabel("変更の依頼（自然文）").fill(prompt);
    await page.getByRole("button", { name: "案を作る" }).click();
  };

  // 1) 存在しない設問を参照するルールの案: 検証エラー。プレビューは出ず、現行データは変わらない。
  await propose(UNKNOWN_PROMPT);
  await expect(page.getByTestId("proposal-error")).toContainText("設問A99は存在しません");
  await expect(page.getByTestId("proposal")).toHaveCount(0);
  await expect(current.getByRole("row")).toHaveCount(4);
  expect(await demoRules()).toEqual(initial);

  // 2) 正しい追加案: 差分を確認し、No。
  await propose(PROMPT);
  await expect(page.getByTestId("proposal")).toBeVisible();
  await expect(page.getByTestId("proposal-error")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /判定ルールの追加案の確認/ })).toBeVisible();
  const before = page.getByRole("region", { name: "Before（現行）" });
  const after = page.getByRole("region", { name: "After（反映した場合）" });
  await expect(before.getByRole("row")).toHaveCount(4);
  await expect(after.getByRole("row")).toHaveCount(5);
  const added = after.getByTestId("After（反映した場合）-R04");
  await expect(added).toContainText("追加");
  await expect(added).toContainText("A09=Yes");
  await expect(added).toContainText("A10=No");
  await expect(added).toHaveClass(/row-added/);
  await expect(page.getByText("反映しますか？")).toBeVisible();
  expect(await demoRules()).toEqual(initial); // 承認前は変わらない
  await page.getByRole("button", { name: "No" }).click();
  await expect(page.getByTestId("proposal")).toHaveCount(0);
  await expect(current.getByRole("row")).toHaveCount(4);
  expect(await demoRules()).toEqual(initial);

  // 3) もう一度作り、Yes で反映される。
  await propose(PROMPT);
  await expect(page.getByTestId("proposal")).toBeVisible();
  await page.getByRole("button", { name: "Yes" }).click();
  await expect(page.getByTestId("proposal-notice")).toContainText("デモの判定ルール一覧に反映しました");
  await expect(page.getByTestId("proposal")).toHaveCount(0);
  await expect(current.getByRole("row")).toHaveCount(5);
  await expect(current.getByTestId("現行のデモの判定ルール一覧-R04")).toBeVisible();

  // 再読み込みしても残る（バックエンドのメモリ。DBではない）。審査画面のルールは変わらない。
  await page.reload();
  await expect(current.getByTestId("現行のデモの判定ルール一覧-R04")).toBeVisible();
  const state = await demoRules();
  expect(state.persistence).toBe("memory");
  const r04 = state.rules.at(-1);
  expect(r04).toMatchObject({ rule_id: "R04", result_code: "caution", conditions: { A09: "yes", A10: "no" } });
  expect(state.rules.slice(0, 3)).toEqual(initial.rules); // 既存のルールは変わらない
  expect(await reviewRuleIds()).toEqual(initialReview);
});
