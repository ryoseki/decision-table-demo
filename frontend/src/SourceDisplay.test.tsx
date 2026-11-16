// 実行: npm test（Vitest + jsdom）。設問管理・判定ルール管理の画面で、生成元（モックか実接続か）と、
// 接続エラーの表示を確認する。APIはモックで、サーバーには接続しない。値はすべて架空。
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import QuestionAdmin from "./QuestionAdmin";
import RuleAdmin from "./RuleAdmin";
import { stubFetch, type Routes } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const bedrock = { kind: "bedrock", model_id: "demo.model-v1:0", region: "region-demo-1" };
const mock = { kind: "mock" };

const questions = Array.from({ length: 12 }, (_, i) => ({ id: `A${String(i + 1).padStart(2, "0")}`, text: `設問${i + 1}？` }));
const newQuestion = { id: "A13", text: "対象期間にAPIキーが失効されたか？" };
const rule = (rule_id: string, result_code: string, conditions: Record<string, "yes" | "no">) => ({
  rule_id,
  revision: 1,
  result_code,
  conditions,
});
const rules = [rule("R01", "caution", { A01: "yes" }), rule("R02", "no_issue", { A03: "yes" })];
const newRule = rule("R03", "caution", { A09: "yes" });

const questionRoutes = (source: unknown): Routes => ({
  "GET /api/admin/questions": { body: { revision: 1, questions, mock: source === mock, persistence: "memory", source } },
  "POST /api/admin/question-proposals": {
    status: 201,
    body: { proposal_id: "p1", base_revision: 1, before: questions, after: [...questions, newQuestion], added: newQuestion, source },
  },
  "POST /api/admin/question-proposals/p1/decision": {
    body: { applied: true, revision: 2, questions: [...questions, newQuestion], source },
  },
});
const ruleRoutes = (source: unknown): Routes => ({
  "GET /api/admin/rules": { body: { revision: 1, rules, mock: source === mock, persistence: "memory", source } },
  "POST /api/admin/rule-proposals": {
    status: 201,
    body: { proposal_id: "p1", kind: "add", base_revision: 1, before: rules, after: [...rules, newRule], added: newRule, source },
  },
  "POST /api/admin/rule-proposals/p1/decision": { body: { applied: true, revision: 2, rules: [...rules, newRule], source } },
});

async function proposeQuestion() {
  await screen.findByText("設問1？");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: "失効の設問を追加して" } });
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));
}
async function proposeRule() {
  await screen.findByText("R02");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: "ルールを追加して" } });
  fireEvent.click(screen.getByRole("button", { name: "案を作る" }));
}

test("設問管理（モック）: 説明は「モックです」。差分に「生成元: モック」が出る", async () => {
  stubFetch(questionRoutes(mock));
  render(<QuestionAdmin />);
  await proposeQuestion();
  const proposal = await screen.findByTestId("proposal");
  expect(within(proposal).getByTestId("source-badge").dataset.source).toBe("mock");
  expect(within(proposal).getByTestId("source-badge").textContent).toContain("生成元: モック");
  expect(screen.getByTestId("mock-note").textContent).toContain("モックです");
  expect(screen.queryByText(/Amazon Bedrock/)).toBeNull();
});

test("設問管理（実接続）: 説明は実接続で、差分に生成元・モデル・リージョンが出る。モックとは書かない", async () => {
  stubFetch(questionRoutes(bedrock));
  render(<QuestionAdmin />);
  await proposeQuestion();
  const proposal = await screen.findByTestId("proposal");
  const badge = within(proposal).getByTestId("source-badge");
  expect(badge.dataset.source).toBe("bedrock");
  expect(badge.textContent).toContain("生成元: Amazon Bedrock（実接続）");
  expect(badge.textContent).toContain("demo.model-v1:0");
  expect(badge.textContent).toContain("region-demo-1");
  const note = screen.getByTestId("mock-note").textContent ?? "";
  expect(note).toContain("実接続です");
  expect(note).toContain("利用料金が発生します");
  expect(note).not.toContain("モックです");
  expect(note).toContain("再起動すると初期状態に戻ります"); // 保存の範囲の説明は、実接続でも残る
});

test("設問管理（実接続）: Yesで反映しても、実接続の説明のまま（モックの説明に戻らない）", async () => {
  stubFetch(questionRoutes(bedrock));
  render(<QuestionAdmin />);
  await proposeQuestion();
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));
  expect((await screen.findByTestId("proposal-notice")).textContent).toContain("反映しました");
  expect(screen.getByTestId("mock-note").textContent).toContain("実接続です");
  expect(screen.queryByTestId("source-badge")).toBeNull(); // プレビューを閉じた
});

test("設問管理: 生成元が分かる前（一覧の読み込み中）は、モックとも実接続とも言わない", async () => {
  stubFetch({ "GET /api/admin/questions": () => new Error("down") });
  render(<QuestionAdmin />);
  expect(screen.getByTestId("mock-note").textContent).toContain("確認しています");
  await screen.findByTestId("list-error");
  expect(screen.getByTestId("mock-note").textContent).not.toContain("モックです");
  expect(screen.getByTestId("mock-note").textContent).not.toContain("実接続です");
});

test("設問管理（実接続）の接続エラー: 原因ごとの固定の文言を出し、成功にも、モックにも見せない", async () => {
  const cases: [number, string, string][] = [
    [504, "AIの応答がタイムアウトしました", "タイムアウトしました"],
    [502, "AIのサービスに接続できませんでした", "AIの応答を得られませんでした"],
    [502, "AIのサービスへのアクセスが許可されていません（権限とモデルの利用設定を確認してください）", "アクセスが許可されていません"],
    [422, "AIの出力をJSONとして読めませんでした", "検証で拒否されました"],
  ];
  for (const [status, detail, expected] of cases) {
    stubFetch({ ...questionRoutes(bedrock), "POST /api/admin/question-proposals": { status, body: { detail } } });
    const { unmount } = render(<QuestionAdmin />);
    await proposeQuestion();
    const text = (await screen.findByTestId("proposal-error")).textContent ?? "";
    expect(text, detail).toContain(expected);
    expect(text, detail).toContain(detail);
    expect(text, detail).toContain("デモの設問一覧は変わっていません");
    expect(text, detail).not.toContain("モック");
    expect(screen.queryByTestId("proposal"), detail).toBeNull(); // 成功の差分を出さない
    expect(screen.queryByTestId("source-badge"), detail).toBeNull(); // 生成元は、成功した提案にだけ付く
    expect(screen.queryByTestId("proposal-notice"), detail).toBeNull();
    unmount();
  }
});

test("判定ルール管理（モック）: 差分に「生成元: モック」が出る", async () => {
  stubFetch(ruleRoutes(mock));
  render(<RuleAdmin />);
  await proposeRule();
  const proposal = await screen.findByTestId("proposal");
  expect(within(proposal).getByTestId("source-badge").dataset.source).toBe("mock");
  expect(screen.getByTestId("mock-note").textContent).toContain("モックです");
});

test("判定ルール管理（実接続）: 説明と差分に、実接続・モデル・リージョンが出る。Yesの後も変わらない", async () => {
  stubFetch(ruleRoutes(bedrock));
  render(<RuleAdmin />);
  await proposeRule();
  const proposal = await screen.findByTestId("proposal");
  const badge = within(proposal).getByTestId("source-badge");
  expect(badge.dataset.source).toBe("bedrock");
  expect(badge.textContent).toContain("demo.model-v1:0");
  expect(badge.textContent).toContain("region-demo-1");
  expect(screen.getByTestId("mock-note").textContent).toContain("実接続です");
  expect(screen.getByTestId("mock-note").textContent).not.toContain("モックです");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));
  expect((await screen.findByTestId("proposal-notice")).textContent).toContain("反映しました");
  expect(screen.getByTestId("mock-note").textContent).toContain("実接続です");
});

test("判定ルール管理（実接続）の接続エラー: 原因ごとの固定の文言（設問管理と同じ網羅）を出す。成功にもモックにも見せない", async () => {
  const cases: [number, string, string][] = [
    [504, "AIの応答がタイムアウトしました", "タイムアウトしました"],
    [502, "AIのサービスに接続できませんでした", "AIの応答を得られませんでした"],
    [502, "AIのサービスへのアクセスが許可されていません（権限とモデルの利用設定を確認してください）", "アクセスが許可されていません"],
    [422, "AIの出力をJSONとして読めませんでした", "検証で拒否されました"],
  ];
  for (const [status, detail, expected] of cases) {
    stubFetch({ ...ruleRoutes(bedrock), "POST /api/admin/rule-proposals": { status, body: { detail } } });
    const { unmount } = render(<RuleAdmin />);
    await proposeRule();
    const text = (await screen.findByTestId("proposal-error")).textContent ?? "";
    expect(text, detail).toContain(expected);
    expect(text, detail).toContain(detail);
    expect(text, detail).toContain("判定ルールの一覧は変わっていません");
    expect(text, detail).not.toContain("モック");
    expect(screen.queryByTestId("proposal"), detail).toBeNull();
    expect(screen.queryByTestId("source-badge"), detail).toBeNull();
    expect(screen.queryByTestId("proposal-notice"), detail).toBeNull();
    unmount();
  }
});

// ---- 廃止案・削除案（実際の操作の流れ。fetch をスタブにして、画面の部品から通す） --------------------------

const retiredQuestion = { ...newQuestion, retired: true };
const withNewQuestion = [...questions, newQuestion];
const retireRoutes = (source: unknown): Routes => ({
  "GET /api/admin/questions": { body: { revision: 2, questions: withNewQuestion, mock: source === mock, persistence: "memory", source } },
  "POST /api/admin/question-proposals": {
    status: 201,
    body: {
      kind: "retire",
      proposal_id: "r1",
      base_revision: 2,
      before: withNewQuestion,
      after: [...questions, retiredQuestion],
      retired: newQuestion,
      references: [],
      can_retire: true,
      impact: { unjudged: [], judged: [] },
      cascade: [],
      mock: source === mock,
      source,
    },
  },
  "POST /api/admin/question-proposals/r1/decision": {
    body: { applied: true, revision: 3, questions: [...questions, retiredQuestion], source },
  },
});

const withNewRule = [...rules, newRule];
const removeRoutes = (source: unknown): Routes => ({
  "GET /api/admin/rules": { body: { revision: 2, rules: withNewRule, mock: source === mock, persistence: "memory", source } },
  "POST /api/admin/rule-proposals": {
    status: 201,
    body: { proposal_id: "d1", kind: "remove", base_revision: 2, before: withNewRule, after: rules, removed: newRule, mock: source === mock, source },
  },
  "POST /api/admin/rule-proposals/d1/decision": { body: { applied: true, revision: 3, rules, source } },
});

// 差分の生成元と、上部の説明が、モック・実接続のどちらかに一致する（もう一方の文言が混ざらない）。
function expectSource(proposal: HTMLElement, kind: "mock" | "bedrock") {
  const badge = within(proposal).getByTestId("source-badge");
  expect(badge.dataset.source).toBe(kind);
  if (kind === "bedrock") {
    expect(badge.textContent).toContain("生成元: Amazon Bedrock（実接続）");
    expect(badge.textContent).toContain("demo.model-v1:0");
    expect(badge.textContent).toContain("region-demo-1");
  } else {
    expect(badge.textContent).toContain("生成元: モック");
    expect(badge.textContent).not.toContain("Bedrock");
  }
  expectNote(kind);
}
function expectNote(kind: "mock" | "bedrock") {
  const note = screen.getByTestId("mock-note").textContent ?? "";
  expect(note).toContain(kind === "bedrock" ? "実接続です" : "モックです");
  expect(note).not.toContain(kind === "bedrock" ? "モックです" : "実接続です");
}

const sources: ["mock" | "bedrock", unknown][] = [
  ["mock", mock],
  ["bedrock", bedrock],
];

for (const [kind, source] of sources) {
  test(`設問の廃止案（${kind}）: 差分に正しい生成元が出て、Yesで廃止しても、説明は変わらない`, async () => {
    stubFetch(retireRoutes(source));
    render(<QuestionAdmin />);
    await screen.findByText(newQuestion.text);
    fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: "設問A13を廃止して" } });
    fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));
    const proposal = await screen.findByTestId("proposal");
    expect(within(proposal).getByRole("heading", { name: /廃止案の確認/ })).toBeTruthy();
    expectSource(proposal, kind);

    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    expect((await screen.findByTestId("proposal-notice")).textContent).toContain("設問A13を廃止");
    expect(screen.queryByTestId("source-badge")).toBeNull();
    expectNote(kind);
  });

  test(`判定ルールの削除案（${kind}）: 差分に正しい生成元が出て、Yesで削除しても、説明は変わらない`, async () => {
    stubFetch(removeRoutes(source));
    render(<RuleAdmin />);
    await screen.findByText("R03");
    fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: "ルールR03を削除して" } });
    fireEvent.click(screen.getByRole("button", { name: "案を作る" }));
    const proposal = await screen.findByTestId("proposal");
    expect(within(proposal).getByRole("heading", { name: /削除案の確認/ })).toBeTruthy();
    expectSource(proposal, kind);

    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    expect((await screen.findByTestId("proposal-notice")).textContent).toContain("ルールR03を削除");
    expect(screen.queryByTestId("source-badge")).toBeNull();
    expectNote(kind);
  });
}

// ---- 一覧の取り直しに失敗したときの、上部の説明 ----------------------------------------------------------

test("設問管理: 競合（409）のあと、一覧の取り直しに失敗したら、「確認しています」のままにせず、確認できなかったと伝える", async () => {
  let listCalls = 0;
  stubFetch({
    ...questionRoutes(bedrock),
    "GET /api/admin/questions": () => {
      listCalls += 1;
      return listCalls === 1 ? { body: { revision: 1, questions, mock: false, persistence: "memory", source: bedrock } } : { status: 500, body: {} };
    },
    "POST /api/admin/question-proposals/p1/decision": { status: 409, body: { detail: "設問の一覧が提案の後に変わったため、反映しませんでした" } },
  });
  render(<QuestionAdmin />);
  await proposeQuestion();
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));
  await screen.findByTestId("list-error");
  const note = screen.getByTestId("mock-note").textContent ?? "";
  expect(note).toContain("確認できませんでした");
  expect(note).not.toContain("確認しています");
  expect(note).not.toContain("モックです");
  expect(note).not.toContain("実接続です");
});

test("判定ルール管理: 競合（409）のあと、一覧の取り直しに失敗したら、「確認しています」のままにせず、確認できなかったと伝える", async () => {
  let listCalls = 0;
  stubFetch({
    ...ruleRoutes(bedrock),
    "GET /api/admin/rules": () => {
      listCalls += 1;
      return listCalls === 1 ? { body: { revision: 1, rules, mock: false, persistence: "memory", source: bedrock } } : { status: 500, body: {} };
    },
    "POST /api/admin/rule-proposals/p1/decision": { status: 409, body: { detail: "判定ルールの一覧が提案の後に変わったため、反映しませんでした" } },
  });
  render(<RuleAdmin />);
  await proposeRule();
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));
  await screen.findByTestId("list-error");
  const note = screen.getByTestId("mock-note").textContent ?? "";
  expect(note).toContain("確認できませんでした");
  expect(note).not.toContain("確認しています");
  expect(note).not.toContain("モックです");
  expect(note).not.toContain("実接続です");
});
