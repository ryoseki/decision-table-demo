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
// 追加するR04は、次のテスト（条件の更新）の準備を兼ねる。A09は、更新の依頼で足すので、ここでは使わない。
const PROMPT = "設問A10がNo、設問A11がYesのとき注意とするルールを追加して";

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
  await expect(page.getByTestId("mock-note")).not.toContainText("実接続です");
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
  await expect(added).toContainText("A10=No");
  await expect(added).toContainText("A11=Yes");
  await expect(added).toHaveClass(/row-added/);
  await expect(page.getByText("反映しますか？")).toBeVisible();
  // 生成元: 既定のモックで動いたことが、差分の確認画面に出る（Bedrockの実接続とは区別される）。
  await expect(page.getByTestId("source-badge")).toHaveAttribute("data-source", "mock");
  await expect(page.getByTestId("source-badge")).toContainText("生成元: モック");
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
  expect(r04).toMatchObject({ rule_id: "R04", result_code: "caution", conditions: { A10: "no", A11: "yes" } });
  expect(state.rules.slice(0, 3)).toEqual(initial.rules); // 既存のルールは変わらない
  expect(await reviewRuleIds()).toEqual(initialReview);
});

// 判定ルールの条件の更新（同じIDのまま、既存ルールに条件を足す）の業務E2E。
//   例の依頼「R04にA09がYesを追加して」→ 更新案（R04が対象。R05は作られない）→ 差分を確認 → No で変わらない
//   → もう一度作り Yes で、R04だけに条件が追加される。再読み込みしても残り、審査画面のルール（GET /api/rules）は変わらない。
//   拒否（対象がない・追加済み）は、理由が出て、現行のルール一覧が変わらない。
// 前提: R04（A10=No・A11=Yes。結果は注意）は、このテスト自身がHTTPで準備する（なければ、追加案を作ってYesを送る。
// 直前のテストが反映済みなら、そのR04を使う）。そのため、このテストだけを新しいバックエンドで実行しても成り立つ。
// 同じバックエンドで更新を反映済みの状態からの再実行はできない（A09が追加済みになる）ので、バックエンドを作り直してから行う。
// 生成は、既定のモック（固定の規則。生成スタブ）。
// 細かい挙動（版の競合、定型依頼の再利用、無効な参照など）は、画面の部品テスト（RuleAdminUpdate.test.tsx）と
// API契約テスト（backend/tests/test_rule_update.py）、DB統合テスト（test_db_store.py）で確認する。
const UPDATE_PROMPT = "R04にA09がYesを追加して";

test("判定ルールの更新案: R04の条件だけが追加され（R05は作られない）、No で変わらず、Yes で反映される。対象がない・追加済みは理由が出て変わらない", async ({
  page,
  request,
}) => {
  type FullRule = ApiRule & { revision: number };
  const demoRules = async () =>
    (await (await request.get(`${backendUrl}/api/admin/rules`)).json()) as { revision: number; rules: FullRule[]; persistence: string };
  const reviewRuleIds = async () =>
    ((await (await request.get(`${backendUrl}/api/rules`)).json()) as { rule_id: string }[]).map((r) => r.rule_id);
  // 準備（画面を使わない）: R04がなければ、追加案を作って承認する。画面の操作は、更新の確認から始める。
  if (!(await demoRules()).rules.some((r) => r.rule_id === "R04")) {
    const created = await request.post(`${backendUrl}/api/admin/rule-proposals`, { data: { prompt: PROMPT } });
    expect(created.status()).toBe(201);
    const { proposal_id } = (await created.json()) as { proposal_id: string };
    const approved = await request.post(`${backendUrl}/api/admin/rule-proposals/${proposal_id}/decision`, { data: { decision: "yes" } });
    expect(approved.status()).toBe(200);
  }
  const initialReview = await reviewRuleIds();
  const initial = await demoRules();
  expect(initial.rules.map((r) => r.rule_id)).toEqual(["R01", "R02", "R03", "R04"]);
  const r04Before = initial.rules[3];
  expect(r04Before).toMatchObject({ revision: 1, result_code: "caution", conditions: { A10: "no", A11: "yes" } });

  await page.goto("/");
  await page.getByRole("link", { name: "判定ルール管理" }).click();
  await expect(page.getByRole("heading", { name: "判定ルール管理（デモ）" })).toBeVisible();
  const current = page.getByRole("region", { name: "現行のデモの判定ルール一覧" });
  await expect(current.getByRole("row")).toHaveCount(5); // 見出し + R01〜R04
  const propose = async (prompt: string) => {
    await page.getByLabel("変更の依頼（自然文）").fill(prompt);
    await page.getByRole("button", { name: "案を作る" }).click();
  };

  // 拒否: 対象のルールがない → 理由が出て、プレビューは出ず、現行のルール一覧は変わらない。
  await propose("R09にA09がYesを追加して");
  await expect(page.getByTestId("proposal-error")).toContainText("ルールID R09 は存在しません");
  await expect(page.getByTestId("proposal")).toHaveCount(0);
  expect(await demoRules()).toEqual(initial);

  // 例の依頼: 更新案。R04が対象で、R05の新設は提案しない。差分に、追加するA09=Yesが出る。
  await propose(UPDATE_PROMPT);
  const proposal = page.getByTestId("proposal");
  await expect(proposal).toBeVisible();
  await expect(page.getByTestId("proposal-error")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /判定ルールの更新案の確認/ })).toBeVisible();
  await expect(page.getByTestId("update-summary")).toContainText("更新の対象: ルールR04");
  await expect(page.getByTestId("update-summary")).toContainText("追加する条件: A09=Yes");
  const before = page.getByRole("region", { name: "Before（現行）" });
  const after = page.getByRole("region", { name: "After（反映した場合）" });
  await expect(before.getByRole("row")).toHaveCount(5);
  await expect(after.getByRole("row")).toHaveCount(5); // ルールの数は同じ（R05は出ない）
  await expect(after.getByTestId("After（反映した場合）-R05")).toHaveCount(0);
  const beforeRow = before.getByTestId("Before（現行）-R04");
  const afterRow = after.getByTestId("After（反映した場合）-R04");
  await expect(beforeRow).toHaveClass(/row-updated/);
  await expect(afterRow).toHaveClass(/row-updated/);
  await expect(afterRow).toContainText("更新");
  await expect(beforeRow).not.toContainText("A09");
  await expect(afterRow.locator("[data-added='true']")).toHaveText(/A09=Yes（追加）/);
  await expect(afterRow).toContainText("A10=No"); // 既存の条件は残る
  await expect(afterRow).toContainText("A11=Yes");
  await expect(afterRow).toContainText("注意"); // 結果は変わらない
  await expect(page.getByTestId("source-badge")).toHaveAttribute("data-source", "mock");
  await expect(page.getByText("反映しますか？")).toBeVisible();
  expect(await demoRules()).toEqual(initial); // 承認前は変わらない

  // No: 承認済みのルールを変更しない。
  await page.getByRole("button", { name: "No" }).click();
  await expect(proposal).toHaveCount(0);
  await expect(page.getByTestId("proposal-notice")).toContainText("この更新案は反映していません");
  await expect(current.getByRole("row")).toHaveCount(5);
  expect(await demoRules()).toEqual(initial);

  // もう一度作り、Yes: R04だけに条件が追加される。R05は作られず、ルールの総数は同じ。
  await propose(UPDATE_PROMPT);
  await expect(proposal).toBeVisible();
  await page.getByRole("button", { name: "Yes" }).click();
  await expect(page.getByTestId("proposal-notice")).toContainText("デモの判定ルール一覧に反映しました（ルールR04の条件を更新）");
  await expect(proposal).toHaveCount(0);
  await expect(current.getByRole("row")).toHaveCount(5);
  await expect(current.getByTestId("現行のデモの判定ルール一覧-R05")).toHaveCount(0);
  const r04Row = current.getByTestId("現行のデモの判定ルール一覧-R04");
  await expect(r04Row.getByRole("cell").nth(0)).toHaveText("2"); // 版は進む
  await expect(r04Row.getByRole("cell").nth(1)).toHaveText("注意");

  // 再読み込みしても残る。R04だけが変わり、他のルールと審査画面のルールは変わらない。
  await page.reload();
  await expect(current.getByTestId("現行のデモの判定ルール一覧-R04")).toBeVisible();
  const state = await demoRules();
  expect(state.rules.map((r) => r.rule_id)).toEqual(["R01", "R02", "R03", "R04"]);
  expect(state.rules.slice(0, 3)).toEqual(initial.rules.slice(0, 3));
  expect(state.rules[3]).toMatchObject({
    rule_id: "R04",
    revision: r04Before.revision + 1,
    result_code: r04Before.result_code,
    conditions: { ...r04Before.conditions, A09: "yes" },
  });
  expect(await reviewRuleIds()).toEqual(initialReview);

  // 拒否: 同じ条件を追加済み → 理由が出て、現行のルール一覧は変わらない。
  await propose(UPDATE_PROMPT);
  await expect(page.getByTestId("proposal-error")).toContainText("追加済み");
  await expect(page.getByTestId("proposal")).toHaveCount(0);
  expect(await demoRules()).toEqual(state);
});
