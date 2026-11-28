import { expect, test } from "@playwright/test";

import { backendUrl } from "../playwright.config";

// 設問管理（デモ）の業務E2E。設問の追加案を作る → 差分を確認 → No で現行データが変わらない →
// もう一度作り、Yes で反映される → 再読み込みしても残る。AIはモック、保存はバックエンドのメモリ（DBなし）。
// 既定のモック（AI_PROVIDER 未設定）では、差分に「生成元: モック」が出る（Bedrockの実接続とは区別される）。
// 実接続（Bedrock）の成功・失敗は、API契約テスト（backend/tests/test_bedrock_proposer.py。偽のクライアント）と、
// 画面の部品テスト（frontend/src/SourceDisplay.test.tsx）で確認し、E2Eでは扱わない（AWSには接続しない）。
// 細かい挙動（エラー表示、競合、再利用の拒否など）は、画面の部品テスト（frontend/src/QuestionAdmin.test.tsx）と
// API契約テスト（backend/tests/test_question_admin.py）で確認する。
const PROMPT = "Add a question that checks the API key revocation history";

test("設問の追加案を作る → 差分を確認 → No で変わらない → もう一度作り Yes で反映（再読み込みでも残る）", async ({ page, request }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Question admin" }).click();
  await expect(page.getByRole("heading", { name: "Question admin (demo)" })).toBeVisible();
  await expect(page.getByTestId("mock-note")).toContainText("This is a mock");
  await expect(page.getByTestId("mock-note")).not.toContainText("Live connection");
  const current = page.getByRole("region", { name: "Current demo question list" });
  await expect(current.getByRole("row")).toHaveCount(13); // 見出し + A01〜A12

  const propose = async () => {
    await page.getByLabel("Change request (plain language)").fill(PROMPT);
    await page.getByRole("button", { name: "Create proposal" }).click();
    await expect(page.getByTestId("proposal")).toBeVisible();
  };

  // 1回目: 差分を確認し、No。
  await propose();
  const before = page.getByRole("region", { name: "Before (current)" });
  const after = page.getByRole("region", { name: "After (if applied)" });
  await expect(before.getByRole("row")).toHaveCount(13);
  await expect(after.getByRole("row")).toHaveCount(14);
  const added = after.getByTestId("After (if applied)-A13");
  await expect(added).toContainText("Added");
  await expect(added).toHaveClass(/row-added/);
  await expect(page.getByText("Apply this change?")).toBeVisible();
  // 生成元: 既定のモックで動いたことが、差分の確認画面に出る。
  const source = page.getByTestId("source-badge");
  await expect(source).toHaveAttribute("data-source", "mock");
  await expect(source).toContainText("Source: Mock");
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
  await expect(page.getByTestId("proposal-notice")).toHaveText("Applied to the demo question list");
  await expect(page.getByTestId("proposal")).toHaveCount(0);
  await expect(current.getByRole("row")).toHaveCount(14);
  await expect(current.getByTestId("Current demo question list-A13")).toBeVisible();

  // 再読み込みしても残る（バックエンドのメモリ。DBではない）。
  await page.reload();
  await expect(current.getByTestId("Current demo question list-A13")).toBeVisible();
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
const FIXTURE_MARKER = "unjudged case for the E2E retirement test";
const RETIRE_PROMPT = "Deactivate question A13";

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
  const current = page.getByRole("region", { name: "Current demo question list" });
  await expect(current.getByTestId("Current demo question list-A13")).toBeVisible();
  await page.getByLabel("Change request (plain language)").fill(RETIRE_PROMPT);
  await page.getByRole("button", { name: "Create proposal" }).click();

  // 差分: 一覧から消さず、A13に「廃止」ラベル。参照するルールはなく、ルールと案件は変更しない。
  await expect(page.getByTestId("proposal")).toBeVisible();
  await expect(page.getByRole("heading", { name: /Proposed change: deactivate question/ })).toBeVisible();
  const after = page.getByRole("region", { name: "After (if applied)" });
  await expect(after.getByTestId("After (if applied)-A13")).toContainText("Deactivating");
  await expect(after.getByRole("row")).toHaveCount(14);
  await expect(page.getByRole("region", { name: "Rules that reference it" })).toContainText("references question");
  await expect(page.getByRole("region", { name: "Impact on existing cases" })).toBeVisible();
  await expect(page.getByTestId("no-cascade")).toContainText("no cascading changes");
  await expect(current.getByTestId("Current demo question list-A13")).not.toContainText("Deactivating"); // 承認前は変わらない

  await page.getByRole("button", { name: "Yes" }).click();
  await expect(page.getByTestId("proposal-notice")).toContainText("Applied to the demo question list");
  await expect(current.getByTestId("Current demo question list-A13")).toContainText("Deactivated");
  await expect(current.getByRole("row")).toHaveCount(14); // 設問文を残す
  await page.reload();
  await expect(current.getByTestId("Current demo question list-A13")).toContainText("Deactivated");

  // 判定未登録の案件は、最新のルール（DBのR01〜R03）で照合できる。
  await page.goto(`/#/cases/${case_id}`);
  await expect(page.getByTestId("save-note")).toBeVisible();
  const apiCase = await (await request.get(`${backendUrl}/api/cases/${case_id}`)).json();
  expect(apiCase.judged).toBe(false);
  // 廃止は案件の保存データを変えない（設問文・回答はそのまま。印だけ付く）。
  expect(apiCase.questions.find((q: { question_code: string }) => q.question_code === "A13")).toMatchObject({
    prompt: "Was an API key revoked during the target period? / 対象期間にAPIキーが失効されたか？",
    answer: "yes",
    retired: true,
  });
  // 廃止した設問は、回答の対象に出ない。保存済みの設問文と回答は、別に読み取り専用で見える。
  await expect(page.getByTestId("q-A13")).toHaveCount(0);
  await expect(page.getByTestId("progress")).toHaveText("0 of 12 answered");
  await expect(page.getByTestId("retired-note")).toContainText("Matching uses the latest rules");
  await expect(page.getByTestId("retired-A13")).toContainText("Was an API key revoked during the target period? / 対象期間にAPIキーが失効されたか？");
  await expect(page.getByTestId("retired-A13")).toContainText("Deactivated");
  await expect(page.getByTestId("retired-answer-A13")).toContainText("Saved answer: Yes");
  await page.getByTestId("q-A01").getByRole("button", { name: "Yes", exact: true }).click();
  await page.getByTestId("q-A03").getByRole("button", { name: "Yes", exact: true }).click();
  await expect(page.getByTestId("rule-R01")).toHaveAttribute("data-display", "normal");
  await expect(page.getByTestId("rule-R03")).toHaveAttribute("data-display", "gray");
});

test("設問の廃止案: 現行ルールが参照する設問は、差分に参照先を示し、Yes でも廃止されない（一覧は変わらない）", async ({ page, request }) => {
  await page.goto("/#/admin/questions");
  const current = page.getByRole("region", { name: "Current demo question list" });
  await expect(current.getByTestId("Current demo question list-A09")).toBeVisible();
  await page.getByLabel("Change request (plain language)").fill("Deactivate question A09");
  await page.getByRole("button", { name: "Create proposal" }).click();
  await expect(page.getByTestId("proposal")).toBeVisible();
  for (const rule of ["R01", "R02", "R03"]) await expect(page.getByTestId(`reference-${rule}`)).toBeVisible();
  await expect(page.getByTestId("retire-blocked")).toContainText("will not deactivate it");

  await page.getByRole("button", { name: "Yes" }).click();
  await expect(page.getByTestId("proposal-error")).toContainText("cannot be deactivated");
  await expect(page.getByTestId("proposal-notice")).toHaveCount(0);
  await expect(current.getByTestId("Current demo question list-A09")).not.toContainText("Deactivated");
  const state = await (await request.get(`${backendUrl}/api/admin/questions`)).json();
  expect(state.questions.find((q: { id: string }) => q.id === "A09").retired).toBeUndefined();
});
