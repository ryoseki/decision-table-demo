import { expect, test } from "@playwright/test";

import { backendUrl } from "../playwright.config";

// 設問管理（デモ）の業務E2E。設問の追加案を作る → 差分を確認 → No で現行データが変わらない →
// もう一度作り、Yes で反映される → 再読み込みしても残る。AIはモック、保存はバックエンドのメモリ（DBなし）。
// 既定のモック（AI_PROVIDER 未設定）では、差分に「生成元: モック」が出る（Bedrockの実接続とは区別される）。
// 実接続（Bedrock）の成功・失敗は、API契約テスト（backend/tests/test_bedrock_proposer.py。偽のクライアント）と、
// 画面の部品テスト（frontend/src/SourceDisplay.test.tsx）で確認し、E2Eでは扱わない（AWSには接続しない）。
// 細かい挙動（エラー表示、競合、再利用の拒否など）は、画面の部品テスト（frontend/src/QuestionAdmin.test.tsx）と
// API契約テスト（backend/tests/test_question_admin.py）で確認する。
const PROMPT = "APIキーの失効履歴を確認する設問を追加して";

test("設問の追加案を作る → 差分を確認 → No で変わらない → もう一度作り Yes で反映（再読み込みでも残る）", async ({ page, request }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "設問管理" }).click();
  await expect(page.getByRole("heading", { name: "設問管理（デモ）" })).toBeVisible();
  await expect(page.getByTestId("mock-note")).toContainText("モックです");
  await expect(page.getByTestId("mock-note")).not.toContainText("実接続です");
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
  // 生成元: 既定のモックで動いたことが、差分の確認画面に出る。
  const source = page.getByTestId("source-badge");
  await expect(source).toHaveAttribute("data-source", "mock");
  await expect(source).toContainText("生成元: モック");
  await expect(source).not.toContainText("Bedrock");
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
  expect(state.source).toEqual({ kind: "mock" }); // APIも、生成元をモックと申告する
});

// 設問の廃止案の業務E2E。参照のない架空の設問A13（上のテストで反映した、APIキーの失効履歴の設問）を、
// Yes で廃止する → 判定未登録の案件を開く → 最新のルールで照合 → 廃止した設問は回答の対象に出ず、
// 保存済みの設問文と回答（Yes）が見える。
// 参照・影響・拒否などの細かい挙動は、画面の部品テスト（frontend/src/QuestionAdmin.test.tsx、CaseDetail.test.tsx）と
// API契約テスト（backend/tests/test_question_retirement.py）で確認する。
// 開発用DBの案件は設問A01〜A12だけを持ち、A01〜A12はすべて判定ルールR01〜R03が参照する。
// そこで、E2E専用のシード（e2e/fixtures/seed_retirement_case.py。compose.e2e.yaml の e2e-fixture）が、
// 参照のない架空の設問A13と、A13に保存済みの回答Yesを持つ判定未登録の案件を、E2E専用のDBにだけ入れる。
const FIXTURE_MARKER = "E2E廃止用の未判定案件";
const RETIRE_PROMPT = "設問A13を廃止して";

const ensureA13 = async (request: import("@playwright/test").APIRequestContext) => {
  const list = await (await request.get(`${backendUrl}/api/admin/questions`)).json();
  if (list.questions.some((q: { id: string }) => q.id === "A13")) return;
  const proposal = await (
    await request.post(`${backendUrl}/api/admin/question-proposals`, { data: { prompt: PROMPT } })
  ).json();
  const decided = await request.post(`${backendUrl}/api/admin/question-proposals/${proposal.proposal_id}/decision`, {
    data: { decision: "yes" },
  });
  expect(decided.status()).toBe(200);
};

test("設問の廃止案: 参照なしの設問を、差分を確認して Yes で廃止 → 判定未登録の案件を開き、最新のルールで照合できる", async ({ page, request }) => {
  await ensureA13(request);
  // E2E専用のシードの案件（判定未登録。A13の回答はYes）。概要の目印で見つける。
  const list = (await (await request.get(`${backendUrl}/api/cases`)).json()) as { case_id: string; summary: string }[];
  const fixture = list.filter((c) => c.summary.includes(FIXTURE_MARKER));
  expect(fixture).toHaveLength(1);
  const case_id = fixture[0].case_id;
  const beforeRetire = await (await request.get(`${backendUrl}/api/cases/${case_id}`)).json();
  expect(beforeRetire.judged).toBe(false);
  expect(beforeRetire.questions.find((q: { question_code: string }) => q.question_code === "A13")).toMatchObject({
    answer: "yes",
    retired: false,
  });

  await page.goto("/#/admin/questions");
  const current = page.getByRole("region", { name: "現行のデモの設問一覧" });
  await expect(current.getByTestId("現行のデモの設問一覧-A13")).toBeVisible();
  await page.getByLabel("変更の依頼（自然文）").fill(RETIRE_PROMPT);
  await page.getByRole("button", { name: "追加案を作る" }).click();

  // 差分: 一覧から消さず、A13に「廃止」ラベル。参照するルールはなく、ルールと案件は変更しない。
  await expect(page.getByTestId("proposal")).toBeVisible();
  await expect(page.getByRole("heading", { name: /廃止案の確認/ })).toBeVisible();
  const after = page.getByRole("region", { name: "After（反映した場合）" });
  await expect(after.getByTestId("After（反映した場合）-A13")).toContainText("廃止");
  await expect(after.getByRole("row")).toHaveCount(14);
  await expect(page.getByRole("region", { name: "参照しているルール" })).toContainText("参照するものはありません");
  await expect(page.getByRole("region", { name: "既存案件への影響" })).toBeVisible();
  await expect(page.getByTestId("no-cascade")).toContainText("連鎖して変えません");
  await expect(current.getByTestId("現行のデモの設問一覧-A13")).not.toContainText("廃止"); // 承認前は変わらない

  await page.getByRole("button", { name: "Yes" }).click();
  await expect(page.getByTestId("proposal-notice")).toContainText("デモの設問一覧に反映しました");
  await expect(current.getByTestId("現行のデモの設問一覧-A13")).toContainText("廃止済み");
  await expect(current.getByRole("row")).toHaveCount(14); // 設問文を残す
  await page.reload();
  await expect(current.getByTestId("現行のデモの設問一覧-A13")).toContainText("廃止済み");

  // 判定未登録の案件は、最新のルール（DBのR01〜R03）で照合できる。
  await page.goto(`/#/cases/${case_id}`);
  await expect(page.getByTestId("save-note")).toBeVisible();
  const apiCase = await (await request.get(`${backendUrl}/api/cases/${case_id}`)).json();
  expect(apiCase.judged).toBe(false);
  // 廃止は案件の保存データを変えない（設問文・回答はそのまま。印だけ付く）。
  expect(apiCase.questions.find((q: { question_code: string }) => q.question_code === "A13")).toMatchObject({
    prompt: "対象期間にAPIキーが失効されたか？",
    answer: "yes",
    retired: true,
  });
  // 廃止した設問は、回答の対象に出ない。保存済みの設問文と回答は、別に読み取り専用で見える。
  await expect(page.getByTestId("q-A13")).toHaveCount(0);
  await expect(page.getByTestId("progress")).toHaveText("0／12問 入力済み");
  await expect(page.getByTestId("retired-note")).toContainText("最新のルールで照合します");
  await expect(page.getByTestId("retired-A13")).toContainText("対象期間にAPIキーが失効されたか？");
  await expect(page.getByTestId("retired-A13")).toContainText("廃止済み");
  await expect(page.getByTestId("retired-answer-A13")).toContainText("保存済みの回答: Yes");
  await page.getByTestId("q-A01").getByRole("button", { name: "Yes", exact: true }).click();
  await page.getByTestId("q-A03").getByRole("button", { name: "Yes", exact: true }).click();
  await expect(page.getByTestId("rule-R01")).toHaveAttribute("data-display", "normal");
  await expect(page.getByTestId("rule-R03")).toHaveAttribute("data-display", "gray");
});

test("設問の廃止案: 現行ルールが参照する設問は、差分に参照先を示し、Yes でも廃止されない（一覧は変わらない）", async ({ page, request }) => {
  await page.goto("/#/admin/questions");
  const current = page.getByRole("region", { name: "現行のデモの設問一覧" });
  await expect(current.getByTestId("現行のデモの設問一覧-A09")).toBeVisible();
  await page.getByLabel("変更の依頼（自然文）").fill("設問A09を廃止して");
  await page.getByRole("button", { name: "追加案を作る" }).click();
  await expect(page.getByTestId("proposal")).toBeVisible();
  for (const rule of ["R01", "R02", "R03"]) await expect(page.getByTestId(`reference-${rule}`)).toBeVisible();
  await expect(page.getByTestId("retire-blocked")).toContainText("廃止されません");

  await page.getByRole("button", { name: "Yes" }).click();
  await expect(page.getByTestId("proposal-error")).toContainText("廃止できません");
  await expect(page.getByTestId("proposal-notice")).toHaveCount(0);
  await expect(current.getByTestId("現行のデモの設問一覧-A09")).not.toContainText("廃止済み");
  const state = await (await request.get(`${backendUrl}/api/admin/questions`)).json();
  expect(state.questions.find((q: { id: string }) => q.id === "A09").retired).toBeUndefined();
});
