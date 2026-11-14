// 実行: npm test（Vitest + jsdom）。設問管理（デモ）の画面の流れを確認する。APIはモックで、サーバーには接続しない。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import QuestionAdmin from "./QuestionAdmin";
import { stubFetch, type Routes } from "./testing";

const baseQuestions = Array.from({ length: 12 }, (_, i) => ({ id: `A${String(i + 1).padStart(2, "0")}`, text: `設問${i + 1}？` }));
const added = { id: "A13", text: "対象期間にAPIキーが失効されたか？" };
const afterQuestions = [...baseQuestions, added];
const PROMPT = "APIキーの失効履歴を確認する設問を追加して";

const listBody = (questions = baseQuestions, revision = 1) => ({ revision, questions, mock: true, persistence: "memory" });
const proposalBody = {
  proposal_id: "p1",
  base_revision: 1,
  before: baseQuestions,
  after: afterQuestions,
  added,
  mock: true,
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
