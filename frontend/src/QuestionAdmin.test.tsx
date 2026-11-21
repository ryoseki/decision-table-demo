// 実行: npm test（Vitest + jsdom）。設問管理（デモ）の画面の流れを確認する。APIはモックで、サーバーには接続しない。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import QuestionAdmin from "./QuestionAdmin";
import { stubFetch, type Routes } from "./testing";

const baseQuestions = Array.from({ length: 12 }, (_, i) => ({ id: `A${String(i + 1).padStart(2, "0")}`, text: `設問${i + 1}？` }));
const added = { id: "A13", text: "対象期間にAPIキーが失効されたか？" };
const afterQuestions = [...baseQuestions, added];
const PROMPT = "APIキーの失効履歴を確認する設問を追加して";

const listBody = (questions = baseQuestions, revision = 1) => ({ revision, questions, mock: true, persistence: "memory", source: { kind: "mock" } });
const proposalBody = {
  proposal_id: "p1",
  base_revision: 1,
  before: baseQuestions,
  after: afterQuestions,
  added,
  mock: true,
  source: { kind: "mock" },
};
const baseRoutes = (): Routes => ({ "GET /api/admin/questions": { body: listBody() } });

beforeEach(() => {
  stubFetch(baseRoutes());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const current = () => screen.getByRole("region", { name: "現行のデモの設問一覧" });
const rowsOf = (region: HTMLElement) => within(region).getAllByRole("row").length - 1; // 見出しの行を除く

async function createProposal() {
  await screen.findByText("設問1？");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: PROMPT } });
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));
  return screen.findByTestId("proposal");
}

test("モックであることと、保存の範囲（メモリ上で、再起動すると初期化）を、画面に出す", async () => {
  render(<QuestionAdmin />);
  await screen.findByText("設問1？"); // 生成元は、一覧の応答で分かる
  const note = screen.getByTestId("mock-note");
  expect(note.textContent).toContain("モックです");
  expect(note.textContent).toContain("実際のAIには接続していません");
  expect(note.textContent).toContain("再起動すると初期状態に戻ります");
  expect(note.textContent).toContain("案件の設問・回答、データベースは変わりません");
  await screen.findByText("設問1？");
});

test("デモの設問一覧を、IDと設問の文言で表示する。プレビューはまだない", async () => {
  render(<QuestionAdmin />);
  expect(await screen.findByText("設問12？")).toBeTruthy();
  expect(rowsOf(current())).toBe(12);
  expect(within(current()).getByText("A01")).toBeTruthy();
  expect(screen.queryByTestId("proposal")).toBeNull();
});

test("依頼が空のあいだは、［追加案を作る］を押せない", async () => {
  render(<QuestionAdmin />);
  await screen.findByText("設問1？");
  const button = screen.getByRole("button", { name: "追加案を作る" }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: "   " } });
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: PROMPT } });
  expect(button.disabled).toBe(false);
});

test("追加案を作ると、Before／Afterの全一覧を出し、追加行を「追加」ラベルと背景で示す。現行の一覧は変わらない", async () => {
  const spy = stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 201, body: proposalBody },
  });
  render(<QuestionAdmin />);
  const proposal = await createProposal();

  expect(within(proposal).getByRole("heading", { name: /追加案の確認/ })).toBeTruthy();
  const before = within(proposal).getByRole("region", { name: "Before（現行）" });
  const after = within(proposal).getByRole("region", { name: "After（反映した場合）" });
  expect(rowsOf(before)).toBe(12);
  expect(rowsOf(after)).toBe(13);
  expect(within(before).queryByText("追加")).toBeNull();
  const row = within(after).getByTestId("After（反映した場合）-A13");
  expect(row.className).toContain("row-added");
  expect(within(row).getByText("追加")).toBeTruthy();
  expect(within(after).getByTestId("After（反映した場合）-A12").className).not.toContain("row-added");
  expect(screen.getByText("反映しますか？")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Yes" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "No" })).toBeTruthy();

  // 承認前は、現行の一覧が変わらず、決定のAPIも呼ばない。
  expect(rowsOf(current())).toBe(12);
  expect(within(current()).queryByText("A13")).toBeNull();
  const posts = spy.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(posts).toHaveLength(1);
  expect(JSON.parse(String(posts[0][1]?.body))).toEqual({ prompt: PROMPT });
});

test("No: 現行の一覧は変わらず、プレビューを閉じる。決定にnoを送る", async () => {
  const spy = stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 201, body: proposalBody },
    "POST /api/admin/question-proposals/p1/decision": { body: { applied: false, ...listBody() } },
  });
  render(<QuestionAdmin />);
  await createProposal();
  fireEvent.click(screen.getByRole("button", { name: "No" }));

  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(screen.getByTestId("proposal-notice").textContent).toContain("この追加案は反映していません");
  expect(screen.queryByText("デモの設問一覧に反映しました")).toBeNull();
  expect(rowsOf(current())).toBe(12);
  expect(within(current()).queryByText("A13")).toBeNull();
  const decision = spy.mock.calls.find(([url]) => String(url).endsWith("/decision"));
  expect(JSON.parse(String(decision?.[1]?.body))).toEqual({ decision: "no" });
});

test("No: 他の人の変更で一覧が進んでいても、「変わっていない」とは言わず、サーバーの最新の一覧を出す", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 201, body: proposalBody },
    "POST /api/admin/question-proposals/p1/decision": { body: { applied: false, ...listBody(afterQuestions, 2) } },
  });
  render(<QuestionAdmin />);
  await createProposal();
  fireEvent.click(screen.getByRole("button", { name: "No" }));

  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  const notice = screen.getByTestId("proposal-notice").textContent ?? "";
  expect(notice).toContain("この追加案は反映していません");
  expect(notice).not.toContain("変わっていません");
  expect(notice).not.toContain("デモの設問一覧に反映しました");
  expect(rowsOf(current())).toBe(13); // 最新の一覧（他の人が追加したA13）
});

test("接続エラー（No）: エラーを出し、閉じたことにしない。プレビューは残る", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 201, body: proposalBody },
    "POST /api/admin/question-proposals/p1/decision": new Error("network down"),
  });
  render(<QuestionAdmin />);
  await createProposal();
  fireEvent.click(screen.getByRole("button", { name: "No" }));

  expect((await screen.findByTestId("proposal-error")).textContent).toContain("サーバーに接続できませんでした");
  expect(screen.queryByTestId("proposal-notice")).toBeNull();
  expect(screen.getByTestId("proposal")).toBeTruthy();
  expect(rowsOf(current())).toBe(12);
});

test("決定の通信エラーは、反映の有無を言い切らない（要求が届いたか分からない）", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 201, body: proposalBody },
    "POST /api/admin/question-proposals/p1/decision": new Error("network down"),
  });
  render(<QuestionAdmin />);
  await createProposal();
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));
  const text = (await screen.findByTestId("proposal-error")).textContent ?? "";
  expect(text).toContain("確認できていません");
  expect(text).not.toContain("変わっていません");
});

test("決定の送信中は、Yes・Noを押せず、二重に送らない。完了すると閉じる", async () => {
  let release: (r: Response) => void = () => {};
  const decisionCalls: RequestInit[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      if (key === "GET /api/admin/questions") return new Response(JSON.stringify(listBody()));
      if (key === "POST /api/admin/question-proposals") return new Response(JSON.stringify(proposalBody), { status: 201 });
      if (key === "POST /api/admin/question-proposals/p1/decision") {
        decisionCalls.push(init ?? {});
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }
      throw new Error(`想定外のリクエスト: ${key}`);
    }),
  );
  render(<QuestionAdmin />);
  await createProposal();
  const yes = screen.getByRole("button", { name: "Yes" }) as HTMLButtonElement;
  const no = screen.getByRole("button", { name: "No" }) as HTMLButtonElement;
  fireEvent.click(yes);
  await waitFor(() => expect(yes.disabled).toBe(true));
  expect(no.disabled).toBe(true);
  fireEvent.click(yes);
  fireEvent.click(no);
  expect(decisionCalls).toHaveLength(1);
  expect(JSON.parse(String(decisionCalls[0].body))).toEqual({ decision: "yes" });

  release(new Response(JSON.stringify({ applied: true, ...listBody(afterQuestions, 2) })));
  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(rowsOf(current())).toBe(13);
});

test("追加案の作成中は、［追加案を作る］を押せず、二重に送らない", async () => {
  let release: (r: Response) => void = () => {};
  const proposalCalls: RequestInit[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      if (key === "GET /api/admin/questions") return new Response(JSON.stringify(listBody()));
      if (key === "POST /api/admin/question-proposals") {
        proposalCalls.push(init ?? {});
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }
      throw new Error(`想定外のリクエスト: ${key}`);
    }),
  );
  render(<QuestionAdmin />);
  await screen.findByText("設問1？");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: PROMPT } });
  const button = screen.getByRole("button", { name: "追加案を作る" }) as HTMLButtonElement;
  fireEvent.click(button);
  await waitFor(() => expect(button.disabled).toBe(true));
  fireEvent.click(button);
  expect(proposalCalls).toHaveLength(1);
  release(new Response(JSON.stringify(proposalBody), { status: 201 }));
  expect(await screen.findByTestId("proposal")).toBeTruthy();
  expect(button.disabled).toBe(false);
});

test("追加案を作り直すと、前のプレビューと通知・エラーは消える", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 201, body: proposalBody },
    "POST /api/admin/question-proposals/p1/decision": { body: { applied: false, ...listBody() } },
  });
  render(<QuestionAdmin />);
  await createProposal();
  fireEvent.click(screen.getByRole("button", { name: "No" }));
  await screen.findByTestId("proposal-notice");
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));
  await screen.findByTestId("proposal");
  expect(screen.queryByTestId("proposal-notice")).toBeNull();
  expect(screen.queryByTestId("proposal-error")).toBeNull();
});

test("Yes: 現行の一覧にA13の行が増え、「デモの設問一覧に反映しました」を出し、プレビューを閉じる", async () => {
  const spy = stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 201, body: proposalBody },
    "POST /api/admin/question-proposals/p1/decision": { body: { applied: true, ...listBody(afterQuestions, 2) } },
  });
  render(<QuestionAdmin />);
  await createProposal();
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(screen.getByTestId("proposal-notice").textContent).toBe("デモの設問一覧に反映しました");
  expect(rowsOf(current())).toBe(13);
  expect(within(current()).getByText(added.text)).toBeTruthy();
  const decision = spy.mock.calls.find(([url]) => String(url).endsWith("/decision"));
  expect(JSON.parse(String(decision?.[1]?.body))).toEqual({ decision: "yes" });
});

test("不正な応答（422）: 検証で拒否した理由を出し、プレビューを出さず、現行の一覧は変わらない", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 422, body: { detail: "設問ID A12 は既に存在します（重複）" } },
  });
  render(<QuestionAdmin />);
  await screen.findByText("設問1？");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: PROMPT } });
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));

  const alert = await screen.findByTestId("proposal-error");
  expect(alert.textContent).toContain("検証で拒否されました");
  expect(alert.textContent).toContain("既に存在します");
  expect(alert.textContent).toContain("変わっていません");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(rowsOf(current())).toBe(12);
});

test("AIの応答を得られない（502）: 成功に見せず、エラーを出す", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 502, body: { detail: "AIの応答を得られませんでした（モック）" } },
  });
  render(<QuestionAdmin />);
  await screen.findByText("設問1？");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: PROMPT } });
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));

  expect((await screen.findByTestId("proposal-error")).textContent).toContain("AIの応答を得られませんでした");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(screen.queryByTestId("proposal-notice")).toBeNull();
});

test("接続エラー（提案の作成）: エラーを出し、プレビューも成功の表示も出さない", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": new Error("network down"),
  });
  render(<QuestionAdmin />);
  await screen.findByText("設問1？");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: PROMPT } });
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));

  expect((await screen.findByTestId("proposal-error")).textContent).toContain("サーバーに接続できませんでした");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(screen.queryByTestId("proposal-notice")).toBeNull();
  expect(rowsOf(current())).toBe(12);
});

test("接続エラー（Yes）: エラーを出し、反映したように見せない。プレビューは残り、現行の一覧は変わらない", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 201, body: proposalBody },
    "POST /api/admin/question-proposals/p1/decision": new Error("network down"),
  });
  render(<QuestionAdmin />);
  await createProposal();
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  expect((await screen.findByTestId("proposal-error")).textContent).toContain("サーバーに接続できませんでした");
  expect(screen.queryByTestId("proposal-notice")).toBeNull();
  expect(screen.getByTestId("proposal")).toBeTruthy();
  expect(rowsOf(current())).toBe(12);
});

test("競合（409）: 反映せず、理由を出し、プレビューを閉じて、最新の一覧を取り直す", async () => {
  const spy = stubFetch({
    "GET /api/admin/questions": () => ({ body: listBody(afterQuestions, 2) }),
    "POST /api/admin/question-proposals": { status: 201, body: proposalBody },
    "POST /api/admin/question-proposals/p1/decision": {
      status: 409,
      body: { detail: "設問の一覧が提案の後に変わったため、反映しませんでした" },
    },
  });
  render(<QuestionAdmin />);
  await createProposal();
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  expect((await screen.findByTestId("proposal-error")).textContent).toContain("反映しませんでした");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(screen.queryByText("デモの設問一覧に反映しました")).toBeNull();
  const gets = () => spy.mock.calls.filter(([url, init]) => String(url) === "/api/admin/questions" && init === undefined);
  await waitFor(() => expect(gets()).toHaveLength(2));
  // 取り直した最新の一覧（A13を含む）を出す。
  expect(await within(current()).findByText(added.text)).toBeTruthy();
});

test("使用済み・未知の提案（404）: エラーを出し、プレビューを閉じる", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 201, body: proposalBody },
    "POST /api/admin/question-proposals/p1/decision": { status: 404, body: { detail: "提案がありません（未知、または処理済みです）" } },
  });
  render(<QuestionAdmin />);
  await createProposal();
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  expect((await screen.findByTestId("proposal-error")).textContent).toContain("提案がありません");
  expect(screen.queryByTestId("proposal")).toBeNull();
});

test("再読み込み相当（画面を作り直す）: 未承認のプレビューは破棄され、現行の一覧が残る", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 201, body: proposalBody },
  });
  const first = render(<QuestionAdmin />);
  await createProposal();
  first.unmount();
  render(<QuestionAdmin />);
  expect(await screen.findByText("設問12？")).toBeTruthy();
  expect(screen.queryByTestId("proposal")).toBeNull();
});

test("一覧の取得に失敗したら、エラーを出す", async () => {
  stubFetch({ "GET /api/admin/questions": { status: 500, body: {} } });
  render(<QuestionAdmin />);
  expect((await screen.findByTestId("list-error")).textContent).toContain("取得できませんでした");
  expect(screen.queryByRole("region", { name: "現行のデモの設問一覧" })).toBeNull();
});

// ---- 設問の廃止案 --------------------------------------------------------------------------------------

const RETIRE_PROMPT = "設問A13を廃止して";
const withA13 = [...baseQuestions, added];
const retiredAfter = [...baseQuestions, { ...added, retired: true }];
const retireBody = (over: Record<string, unknown> = {}) => ({
  kind: "retire",
  proposal_id: "r1",
  base_revision: 2,
  before: withA13,
  after: retiredAfter,
  retired: added,
  references: [],
  can_retire: true,
  impact: {
    unjudged: [{ case_id: "CASE-U1", summary: "未登録の架空案件", answer: "yes" }],
    judged: [{ case_id: "CASE-J1", summary: "登録済みの架空案件", answer: "no", result_code: "no_issue" }],
  },
  cascade: [],
  mock: true,
  ...over,
});
const retireRoutes = (body = retireBody()): Routes => ({
  "GET /api/admin/questions": { body: listBody(withA13, 2) },
  "POST /api/admin/question-proposals": { status: 201, body },
});

async function createRetirement() {
  await screen.findByText(added.text);
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: RETIRE_PROMPT } });
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));
  return screen.findByTestId("proposal");
}

test("廃止案: Before／Afterの差分で対象行に「廃止」ラベルを付け、一覧から消さない。現行の一覧は変わらない", async () => {
  stubFetch(retireRoutes());
  render(<QuestionAdmin />);
  const proposal = await createRetirement();
  expect(within(proposal).getByRole("heading", { name: /廃止案の確認/ })).toBeTruthy();
  const before = within(proposal).getByRole("region", { name: "Before（現行）" });
  const after = within(proposal).getByRole("region", { name: "After（反映した場合）" });
  expect(rowsOf(before)).toBe(13);
  expect(rowsOf(after)).toBe(13); // 消さずに残す
  const row = within(after).getByTestId("After（反映した場合）-A13");
  expect(row.className).toContain("row-retired");
  expect(within(row).getByText("廃止")).toBeTruthy();
  expect(row.textContent).toContain(added.text); // 設問文は残る
  expect(within(before).queryByText("廃止")).toBeNull();
  expect(within(current()).queryByText("廃止")).toBeNull(); // 承認前は、現行の一覧が変わらない
  expect(screen.getByText("反映しますか？")).toBeTruthy();
});

test("廃止案: 参照するルールがなければ、その旨を示す（拒否の警告は出さない）", async () => {
  stubFetch(retireRoutes());
  render(<QuestionAdmin />);
  await createRetirement();
  const refs = screen.getByRole("region", { name: "参照しているルール" });
  expect(refs.textContent).toContain("参照するものはありません");
  expect(screen.queryByTestId("retire-blocked")).toBeNull();
});

test("廃止案: 参照するルールを、ルールID・版・結果で示し、Yesでも廃止されないと警告する", async () => {
  const body = retireBody({
    references: [{ rule_id: "R01", revision: 2, result_code: "caution" }],
    can_retire: false,
  });
  stubFetch(retireRoutes(body));
  render(<QuestionAdmin />);
  await createRetirement();
  const row = screen.getByTestId("reference-R01");
  expect(row.textContent).toContain("R01");
  expect(row.textContent).toContain("2");
  expect(row.textContent).toContain("注意");
  const warning = screen.getByTestId("retire-blocked");
  expect(warning.textContent).toContain("参照するルールが1件あります");
  expect(warning.textContent).toContain("Yesを選んでも");
  expect(warning.textContent).toContain("廃止されません");
});

test("廃止案: 参照があって廃止できない案でも、既存案件への影響を同時に示す（案件の一覧は隠さない）", async () => {
  stubFetch(
    retireRoutes(
      retireBody({ references: [{ rule_id: "R01", revision: 1, result_code: "caution" }], can_retire: false }),
    ),
  );
  render(<QuestionAdmin />);
  await createRetirement();
  expect(screen.getByTestId("retire-blocked").textContent).toContain("廃止されません");
  const impact = screen.getByRole("region", { name: "既存案件への影響" });
  expect(impact.textContent).toContain("判定未登録の案件（1件）");
  expect(impact.textContent).toContain("判定登録済みの案件（1件）");
  expect(screen.getByTestId("impact-CASE-U1").textContent).toContain("保存済みの回答: Yes");
  expect(screen.getByTestId("impact-CASE-J1").textContent).toContain("登録時の結果: 問題なし");
  expect(screen.getByTestId("no-cascade")).toBeTruthy();
});

test("廃止案: 既存案件への影響を、判定未登録と判定登録済みに分け、保存済みの回答と登録時の結果を示す。連鎖して変えない旨も示す", async () => {
  stubFetch(retireRoutes());
  render(<QuestionAdmin />);
  await createRetirement();
  const impact = screen.getByRole("region", { name: "既存案件への影響" });
  expect(impact.textContent).toContain("判定未登録の案件（1件）");
  expect(impact.textContent).toContain("判定登録済みの案件（1件）");
  expect(impact.textContent).toContain("最新のルールで照合します");
  expect(impact.textContent).toContain("保存済みの設問文と回答は、審査画面で確認できます");
  expect(impact.textContent).toContain("登録時の結果と、保存済みの設問文・回答を表示します");
  const unjudged = screen.getByTestId("impact-CASE-U1");
  expect(unjudged.textContent).toContain("保存済みの回答: Yes");
  expect(unjudged.textContent).not.toContain("登録時の結果");
  const link = within(unjudged).getByRole("link", { name: "CASE-U1" });
  expect(link.getAttribute("href")).toBe("#/cases/CASE-U1");
  expect(link.getAttribute("title")).toBe("CASE-U1");
  const judged = screen.getByTestId("impact-CASE-J1");
  expect(judged.textContent).toContain("保存済みの回答: No");
  expect(judged.textContent).toContain("登録時の結果: 問題なし");
  expect(screen.getByTestId("no-cascade").textContent).toContain("連鎖して変えません");
});

test("廃止案: 影響を受ける案件がなければ、0件と示す", async () => {
  stubFetch(retireRoutes(retireBody({ impact: { unjudged: [], judged: [] } })));
  render(<QuestionAdmin />);
  await createRetirement();
  const impact = screen.getByRole("region", { name: "既存案件への影響" });
  expect(impact.textContent).toContain("判定未登録の案件（0件）");
  expect(impact.textContent).toContain("判定登録済みの案件（0件）");
});

test("廃止案 Yes: 廃止を反映し、一覧に設問を「廃止済み」で残す。決定にyesを送る", async () => {
  const spy = stubFetch({
    ...retireRoutes(),
    "POST /api/admin/question-proposals/r1/decision": { body: { applied: true, ...listBody(retiredAfter, 3) } },
  });
  render(<QuestionAdmin />);
  await createRetirement();
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));
  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(screen.getByTestId("proposal-notice").textContent).toContain("デモの設問一覧に反映しました（設問A13を廃止");
  expect(rowsOf(current())).toBe(13);
  const row = within(current()).getByTestId("現行のデモの設問一覧-A13");
  expect(within(row).getByText("廃止済み")).toBeTruthy();
  expect(row.textContent).toContain(added.text);
  const decision = spy.mock.calls.find(([url]) => String(url).endsWith("/decision"));
  expect(JSON.parse(String(decision?.[1]?.body))).toEqual({ decision: "yes" });
});

test("廃止案 No: 現行の一覧は変わらず、「廃止案は反映していません」と示す。決定にnoを送る", async () => {
  const spy = stubFetch({
    ...retireRoutes(),
    "POST /api/admin/question-proposals/r1/decision": { body: { applied: false, ...listBody(withA13, 2) } },
  });
  render(<QuestionAdmin />);
  await createRetirement();
  fireEvent.click(screen.getByRole("button", { name: "No" }));
  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(screen.getByTestId("proposal-notice").textContent).toContain("この廃止案は反映していません");
  expect(within(current()).queryByText("廃止済み")).toBeNull();
  expect(rowsOf(current())).toBe(13);
  const decision = spy.mock.calls.find(([url]) => String(url).endsWith("/decision"));
  expect(JSON.parse(String(decision?.[1]?.body))).toEqual({ decision: "no" });
});

test("廃止案 Yes が拒否（参照あり・409）されたら、成功と言わず、拒否の理由を出し、一覧は廃止済みにしない", async () => {
  const detail = "設問A13は、現行の判定ルール（R01）が参照しているため、廃止できません。設問の一覧は変わっていません";
  stubFetch({
    ...retireRoutes(retireBody({ references: [{ rule_id: "R01", revision: 2, result_code: "caution" }], can_retire: false })),
    "POST /api/admin/question-proposals/r1/decision": { status: 409, body: { detail } },
  });
  render(<QuestionAdmin />);
  await createRetirement();
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));
  expect((await screen.findByTestId("proposal-error")).textContent).toContain("廃止できません");
  expect(screen.getByTestId("proposal-error").textContent).toContain("R01");
  expect(screen.queryByTestId("proposal-notice")).toBeNull();
  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  await waitFor(() => expect(within(current()).getByTestId("現行のデモの設問一覧-A13")).toBeTruthy());
  expect(within(current()).queryByText("廃止済み")).toBeNull();
});

test("設問一覧: 既に廃止済みの設問は「廃止済み」ラベルで示し、設問文を残す", async () => {
  stubFetch({ "GET /api/admin/questions": { body: listBody(retiredAfter, 3) } });
  render(<QuestionAdmin />);
  const region = await screen.findByRole("region", { name: "現行のデモの設問一覧" });
  const row = await within(region).findByTestId("現行のデモの設問一覧-A13");
  expect(within(row).getByText("廃止済み")).toBeTruthy();
  expect(row.className).toContain("row-retired");
  expect(row.textContent).toContain(added.text);
  expect(within(region).getAllByText("廃止済み")).toHaveLength(1); // ほかの設問には付けない
});

test("追加案は、従来どおり（追加ラベル）で、廃止の補足（参照・影響）は出さない", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 201, body: { ...proposalBody, kind: "add" } },
  });
  render(<QuestionAdmin />);
  await createProposal();
  expect(screen.queryByTestId("retirement-detail")).toBeNull();
  expect(within(screen.getByTestId("proposal")).getByText("追加")).toBeTruthy();
});


test("応答を受け取れなかった提案の再送には同じ依頼IDを使う", async () => {
  const keys: Array<string | null> = [];
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": (init) => {
      keys.push(new Headers(init?.headers).get("Idempotency-Key"));
      return keys.length === 1 ? new Error("response lost") : { body: proposalBody };
    },
  });
  render(<QuestionAdmin />);
  await screen.findByText("設問1？");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: PROMPT } });
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));
  await screen.findByTestId("proposal");
  expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/);
  expect(keys).toEqual([keys[0], keys[0]]);
});


async function submitTwice(replies: Array<Error | { status: number; body: unknown }>) {
  const keys: Array<string | null> = [];
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": (init) => {
      keys.push(new Headers(init?.headers).get("Idempotency-Key"));
      return replies[keys.length - 1] ?? { status: 201, body: proposalBody };
    },
  });
  render(<QuestionAdmin />);
  await screen.findByText("設問1？");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: PROMPT } });
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));
  await waitFor(() => expect(keys).toHaveLength(2));
  return keys;
}

test("接続できない・503（結果が不明）は、同じ入力の再送で同じ依頼IDを使う", async () => {
  for (const reply of [new Error("offline"), { status: 503, body: { detail: "混み合っています" } }]) {
    const keys = await submitTwice([reply]);
    expect(keys[1]).toBe(keys[0]);
    cleanup();
  }
});

test("502・504・429・409など、サーバーが結果を返した失敗の後は、新しい依頼IDにする", async () => {
  for (const status of [502, 504, 429, 409, 500]) {
    const keys = await submitTwice([{ status, body: { detail: "理由" } }]);
    expect(keys[1], String(status)).not.toBe(keys[0]);
    expect(keys[1], String(status)).toMatch(/^[0-9a-f-]{36}$/);
    cleanup();
  }
});

test("入力を変えた再送は、結果が不明でも新しい依頼IDにする", async () => {
  const keys: Array<string | null> = [];
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": (init) => {
      keys.push(new Headers(init?.headers).get("Idempotency-Key"));
      return new Error("offline");
    },
  });
  render(<QuestionAdmin />);
  await screen.findByText("設問1？");
  const box = screen.getByLabelText("変更の依頼（自然文）");
  fireEvent.change(box, { target: { value: PROMPT } });
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));
  await screen.findByRole("alert");
  fireEvent.change(box, { target: { value: `${PROMPT}（変更）` } });
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));
  await waitFor(() => expect(keys).toHaveLength(2));
  expect(keys[1]).not.toBe(keys[0]);
});

test("送信中は、ボタンを無効にし、フォームの二重送信でも要求は1回だけ（busyの柵）", async () => {
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const spy = stubFetch(baseRoutes());
  spy.mockImplementation(async (input, init) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    if (key === "GET /api/admin/questions") return new Response(JSON.stringify(listBody()), { status: 200 });
    if (key === "GET /api/rules") return new Response("[]", { status: 200 });
    await gate;
    return new Response(JSON.stringify(proposalBody), { status: 201 });
  });
  render(<QuestionAdmin />);
  await screen.findByText("設問1？");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: PROMPT } });
  const button = screen.getByRole("button", { name: "追加案を作る" });
  fireEvent.click(button);
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.submit(button.closest("form") as HTMLFormElement);
  fireEvent.submit(button.closest("form") as HTMLFormElement);
  release?.();
  await screen.findByTestId("proposal");
  const posts = spy.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(posts).toHaveLength(1);
});

test("利用枠の上限（429）と送信済み（409）は、サーバーの文言を、そのまま画面に出す", async () => {
  const quota = "この接続元からの、今日（日本時間）のAI提案の回数の上限に達しました。共有データは変わっていません";
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 429, body: { detail: quota } },
  });
  render(<QuestionAdmin />);
  await screen.findByText("設問1？");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: PROMPT } });
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));
  expect((await screen.findByRole("alert")).textContent).toBe(quota);
  expect(screen.queryByTestId("proposal")).toBeNull();
  cleanup();
  const sent = "この依頼は送信済みです。AIの呼び出しは繰り返しません";
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": { status: 409, body: { detail: sent } },
  });
  render(<QuestionAdmin />);
  await screen.findByText("設問1？");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: PROMPT } });
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));
  expect((await screen.findByRole("alert")).textContent).toBe(sent);
});


test("定型案のNoでは生成せず、明示的なAI再提案だけが振り分けを迂回する", async () => {
  const generated: unknown[] = [];
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/question-proposals": (init) => {
      const body = JSON.parse(init?.body as string);
      generated.push(body);
      return { body: { ...proposalBody, source: { kind: body.force_ai ? "bedrock" : "template" } } };
    },
    "POST /api/admin/question-proposals/p1/decision": { body: { ...listBody(), applied: false } },
  });
  render(<QuestionAdmin />);
  await createProposal();
  expect(screen.getByTestId("source-badge").getAttribute("data-source")).toBe("template");
  fireEvent.click(screen.getByRole("button", { name: "No", exact: true }));
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: "別の文" } });
  const button = await screen.findByRole("button", { name: "AIで再提案" });
  expect(generated).toEqual([{ prompt: PROMPT }]);
  fireEvent.click(button);
  await screen.findByTestId("proposal");
  expect(generated).toEqual([{ prompt: PROMPT }, { prompt: PROMPT, force_ai: true }]);
  expect(screen.getByTestId("source-badge").getAttribute("data-source")).toBe("bedrock");
});


const TEMPLATE_HEADING = "AIを経由せずに反映する変更の依頼(完全一致)";
const TEMPLATE_NOTE = "意図と異なる変更案が出る場合は、該当する依頼を削除してください。";

test("定型文の一覧は初期状態で閉じ、指定の見出しと案内を出す。登録の操作はない", async () => {
  stubFetch({
    "GET /api/admin/questions": { body: { ...listBody(), template_reuse: true } },
    "GET /api/admin/proposal-templates/question": { body: { prompts: ["設問A13を廃止して"] } },
  });
  render(<QuestionAdmin />);
  const summary = await screen.findByText(TEMPLATE_HEADING);
  const details = summary.closest("details")!;
  expect(summary.tagName).toBe("SUMMARY");
  expect(details.open).toBe(false);
  expect(within(details).getByText(TEMPLATE_NOTE).closest("small")).not.toBeNull();
  await screen.findByRole("button", { name: "設問A13を廃止してを定型文から削除" });
  expect(screen.queryByRole("button", { name: "再利用に登録" })).toBeNull();
});

test("Yesで反映すると、自動登録された定型文の一覧を取得し直し、削除で一覧を更新する。Noでは取得し直さない", async () => {
  const templatePath = "/api/admin/proposal-templates/question";
  let templateReads = 0;
  const spy = stubFetch({
    "GET /api/admin/questions": { body: { ...listBody(), template_reuse: true } },
    ["GET " + templatePath]: () => {
      templateReads += 1;
      return { body: { prompts: templateReads === 1 ? [] : [PROMPT] } };
    },
    "POST /api/admin/question-proposals": { body: { ...proposalBody, source: { kind: "bedrock" } } },
    "POST /api/admin/question-proposals/p1/decision": {
      body: { ...listBody(afterQuestions, 2), applied: true, template_reuse: true },
    },
    ["POST " + templatePath + "/remove"]: (init) => {
      expect(JSON.parse(init?.body as string)).toEqual({ prompt: PROMPT });
      return { body: { prompts: [] } };
    },
  });
  render(<QuestionAdmin />);
  await createProposal();
  await waitFor(() => expect(templateReads).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "Yes", exact: true }));
  const remove = await screen.findByRole("button", { name: PROMPT + "を定型文から削除" });
  expect(templateReads).toBe(2);
  // 登録の要求は、画面から送らない（サーバーが反映と同時に登録する）。
  expect(spy.mock.calls.some(([url, init]) => url === templatePath && init?.method === "POST")).toBe(false);
  fireEvent.click(remove);
  await waitFor(() => expect(screen.queryByRole("button", { name: PROMPT + "を定型文から削除" })).toBeNull());
});

test("Noでは、定型文の一覧を取得し直さない", async () => {
  const templatePath = "/api/admin/proposal-templates/question";
  const spy = stubFetch({
    "GET /api/admin/questions": { body: { ...listBody(), template_reuse: true } },
    ["GET " + templatePath]: { body: { prompts: [] } },
    "POST /api/admin/question-proposals": { body: { ...proposalBody, source: { kind: "bedrock" } } },
    "POST /api/admin/question-proposals/p1/decision": { body: { ...listBody(), applied: false, template_reuse: true } },
  });
  render(<QuestionAdmin />);
  await createProposal();
  fireEvent.click(screen.getByRole("button", { name: "No", exact: true }));
  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(spy.mock.calls.filter(([url]) => url === templatePath)).toHaveLength(1);
});
