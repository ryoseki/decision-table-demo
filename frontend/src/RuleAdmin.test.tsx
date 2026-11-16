// 実行: npm test（Vitest + jsdom）。判定ルール管理（デモ）の画面の流れを確認する。APIはモックで、サーバーには接続しない。
// 設問管理と同じ流れ（差分 → Yes／No）で、操作の対象が判定ルールであることを確認する。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import RuleAdmin from "./RuleAdmin";
import { stubFetch, type Routes } from "./testing";

type Cond = Record<string, "yes" | "no">;
const rule = (rule_id: string, result_code: string, conditions: Cond) => ({ rule_id, revision: 1, result_code, conditions });
const baseRules = [
  rule("R01", "caution", { A01: "yes", A02: "no" }),
  rule("R02", "no_issue", { A03: "yes" }),
  rule("R03", "account_suspension", { A04: "no" }),
];
const added = rule("R04", "caution", { A09: "yes", A10: "no" });
const afterRules = [...baseRules, added];
const PROMPT = "設問A09がYes、設問A10がNoのとき注意とするルールを追加して";

const listBody = (rules = baseRules, revision = 1) => ({ revision, rules, mock: true, persistence: "memory", source: { kind: "mock" } });
const addBody = { proposal_id: "p1", kind: "add", base_revision: 1, before: baseRules, after: afterRules, added, mock: true, source: { kind: "mock" } };
const removeBody = {
  proposal_id: "p2",
  kind: "remove",
  base_revision: 1,
  before: baseRules,
  after: baseRules.slice(0, 2),
  removed: baseRules[2],
  mock: true,
};
const baseRoutes = (): Routes => ({ "GET /api/admin/rules": { body: listBody() } });

beforeEach(() => {
  stubFetch(baseRoutes());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const current = () => screen.getByRole("region", { name: "現行のデモの判定ルール一覧" });
const rowsOf = (region: HTMLElement) => within(region).getAllByRole("row").length - 1; // 見出しの行を除く

async function createProposal(prompt = PROMPT) {
  await screen.findByText("R03");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: prompt } });
  fireEvent.click(screen.getByRole("button", { name: "案を作る" }));
}

test("操作の対象が判定ルールであることと、モック・保存の範囲・審査画面に影響しないことを出す", async () => {
  render(<RuleAdmin />);
  expect(screen.getByRole("heading", { name: "判定ルール管理（デモ）" })).toBeTruthy();
  expect(screen.getByTestId("target").textContent).toBe("操作の対象: 判定ルール");
  await screen.findByText("R03"); // 生成元は、一覧の応答で分かる
  const note = screen.getByTestId("mock-note").textContent ?? "";
  expect(note).toContain("モックです");
  expect(note).toContain("再起動すると初期状態に戻ります");
  expect(note).toContain("審査画面で照合に使うルール、案件、データベースは変わりません");
  await screen.findByText("R03");
});

test("現行のデモのルール一覧を、ルールID・版・結果の表示名・条件（設問＝回答）で出す。プレビューはまだない", async () => {
  render(<RuleAdmin />);
  await screen.findByText("R03");
  expect(rowsOf(current())).toBe(3);
  const r1 = within(current()).getByTestId("現行のデモの判定ルール一覧-R01");
  expect(r1.textContent).toContain("注意");
  expect(within(current()).getByTestId("現行のデモの判定ルール一覧-R03").textContent).toContain("アカウント一時凍結");
  expect(screen.queryByTestId("proposal")).toBeNull();
});

const headers = (region: HTMLElement) => within(region).getAllByRole("columnheader").map((h) => h.textContent);
const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((c) => c.textContent);
const FIXED = Array.from({ length: 12 }, (_, i) => `A${String(i + 1).padStart(2, "0")}`);
const dash = (n: number) => Array<string>(n).fill("-");

test("現行の一覧は、ルール・版・結果の後にA01〜A12を固定の12列で並べ、条件をYes／No、条件なしを - で出す", async () => {
  render(<RuleAdmin />);
  await screen.findByText("R03");
  expect(headers(current())).toEqual(["ルール", "版", "結果", ...FIXED]);
  const row = (id: string) => within(current()).getByTestId(`現行のデモの判定ルール一覧-${id}`);
  expect(cells(row("R01"))).toEqual(["1", "注意", "Yes", "No", ...dash(10)]);
  expect(cells(row("R02"))).toEqual(["1", "問題なし", "-", "-", "Yes", ...dash(9)]);
  expect(cells(row("R03"))).toEqual(["1", "アカウント一時凍結", "-", "-", "-", "No", ...dash(8)]);
  // Yes／Noは操作できないラベル（ボタンではない）。条件なしの - はラベルにしない。
  const tds = row("R01").querySelectorAll("td");
  expect(tds[2].querySelector(".answer-chip.answer-yes")?.textContent).toBe("Yes");
  expect(tds[3].querySelector(".answer-chip.answer-no")?.textContent).toBe("No");
  expect(tds[4].querySelector(".answer-chip")).toBeNull();
  expect(within(row("R01")).queryAllByRole("button")).toHaveLength(0);
  // 横スクロールは、フォーカスできる現行表のラッパーの中だけ。
  const wrap = within(current()).getByRole("group", { name: "現行のデモの判定ルール一覧の表（横にスクロールできます）" });
  expect(wrap.getAttribute("tabindex")).toBe("0");
  expect(wrap.querySelector("table")).not.toBeNull();
});

test("A13以降の条件を持つルールがあるときだけ、A12の後ろに自然順で列を足す", async () => {
  const rules = [rule("R01", "caution", { A13: "yes", A02: "no" }), rule("R02", "no_issue", { A100: "no", A13: "no" })];
  stubFetch({ "GET /api/admin/rules": { body: listBody(rules) } });
  render(<RuleAdmin />);
  await screen.findByText("R02");
  expect(headers(current())).toEqual(["ルール", "版", "結果", ...FIXED, "A13", "A100"]);
  const row = (id: string) => within(current()).getByTestId(`現行のデモの判定ルール一覧-${id}`);
  expect(cells(row("R01"))).toEqual(["1", "注意", "-", "No", ...dash(10), "Yes", "-"]);
  expect(cells(row("R02"))).toEqual(["1", "問題なし", ...dash(12), "No", "No"]);
});

test("Before／Afterの表は、これまでどおり1列の条件（設問＝回答）で出す", async () => {
  stubFetch({ ...baseRoutes(), "POST /api/admin/rule-proposals": { status: 201, body: addBody } });
  render(<RuleAdmin />);
  await createProposal();
  const after = await screen.findByRole("region", { name: "After（反映した場合）" });
  expect(headers(after)).toEqual(["ルール", "版", "結果", "条件（設問＝回答）"]);
});

test("依頼が空のあいだは、［案を作る］を押せない", async () => {
  render(<RuleAdmin />);
  await screen.findByText("R03");
  const button = screen.getByRole("button", { name: "案を作る" }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: "  " } });
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: PROMPT } });
  expect(button.disabled).toBe(false);
});

test("追加案: Before／Afterの全一覧を出し、追加行を「追加」ラベルと背景で示す。現行の一覧は変わらず、決定も送らない", async () => {
  const spy = stubFetch({ ...baseRoutes(), "POST /api/admin/rule-proposals": { status: 201, body: addBody } });
  render(<RuleAdmin />);
  await createProposal();
  const proposal = await screen.findByTestId("proposal");

  expect(within(proposal).getByRole("heading", { name: /判定ルールの追加案の確認/ })).toBeTruthy();
  const before = within(proposal).getByRole("region", { name: "Before（現行）" });
  const after = within(proposal).getByRole("region", { name: "After（反映した場合）" });
  expect(rowsOf(before)).toBe(3);
  expect(rowsOf(after)).toBe(4);
  const row = within(after).getByTestId("After（反映した場合）-R04");
  expect(row.className).toContain("row-added");
  expect(within(row).getByText("追加")).toBeTruthy();
  expect(row.textContent).toContain("A09=Yes");
  expect(row.textContent).toContain("A10=No");
  expect(within(after).getByTestId("After（反映した場合）-R03").className).not.toContain("row-added");
  expect(screen.getByText("反映しますか？")).toBeTruthy();

  expect(rowsOf(current())).toBe(3);
  expect(within(current()).queryByText("R04")).toBeNull();
  const posts = spy.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(posts).toHaveLength(1);
  expect(String(posts[0][0])).toBe("/api/admin/rule-proposals");
  expect(JSON.parse(String(posts[0][1]?.body))).toEqual({ prompt: PROMPT });
});

test("削除案: 削除するルールをBeforeで「削除」ラベルと背景で示し、Afterには出ない。現行の一覧は変わらない", async () => {
  stubFetch({ ...baseRoutes(), "POST /api/admin/rule-proposals": { status: 201, body: removeBody } });
  render(<RuleAdmin />);
  await createProposal("ルールR03を削除して");
  const proposal = await screen.findByTestId("proposal");

  expect(within(proposal).getByRole("heading", { name: /判定ルールの削除案の確認/ })).toBeTruthy();
  const before = within(proposal).getByRole("region", { name: "Before（現行）" });
  const after = within(proposal).getByRole("region", { name: "After（反映した場合）" });
  const row = within(before).getByTestId("Before（現行）-R03");
  expect(row.className).toContain("row-retired");
  expect(within(row).getByText("削除")).toBeTruthy();
  expect(within(after).queryByText("R03")).toBeNull();
  expect(rowsOf(after)).toBe(2);
  expect(rowsOf(current())).toBe(3);
});

test("Yes: 決定にyesを送り、反映の表示と最新の一覧（追加したルール）を出して、プレビューを閉じる", async () => {
  const spy = stubFetch({
    ...baseRoutes(),
    "POST /api/admin/rule-proposals": { status: 201, body: addBody },
    "POST /api/admin/rule-proposals/p1/decision": { body: { applied: true, ...listBody(afterRules, 2) } },
  });
  render(<RuleAdmin />);
  await createProposal();
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(screen.getByTestId("proposal-notice").textContent).toBe("デモの判定ルール一覧に反映しました（ルールR04を追加）");
  expect(rowsOf(current())).toBe(4);
  expect(within(current()).getByTestId("現行のデモの判定ルール一覧-R04")).toBeTruthy();
  const decision = spy.mock.calls.find(([url]) => String(url).endsWith("/decision"));
  expect(JSON.parse(String(decision?.[1]?.body))).toEqual({ decision: "yes" });
});

test("No: 現行の一覧は変わらず、プレビューを閉じる。決定にnoを送り、「反映した」とは言わない", async () => {
  const spy = stubFetch({
    ...baseRoutes(),
    "POST /api/admin/rule-proposals": { status: 201, body: addBody },
    "POST /api/admin/rule-proposals/p1/decision": { body: { applied: false, ...listBody() } },
  });
  render(<RuleAdmin />);
  await createProposal();
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "No" }));

  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(screen.getByTestId("proposal-notice").textContent).toContain("この追加案は反映していません");
  expect(screen.queryByText(/に反映しました/)).toBeNull();
  expect(rowsOf(current())).toBe(3);
  const decision = spy.mock.calls.find(([url]) => String(url).endsWith("/decision"));
  expect(JSON.parse(String(decision?.[1]?.body))).toEqual({ decision: "no" });
});

test("検証エラー（422）: 理由を出し、プレビューは出さず、現行の一覧は変わらない", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/rule-proposals": { status: 422, body: { detail: "設問A99は存在しません（ルールは参照できません）" } },
  });
  render(<RuleAdmin />);
  await createProposal("設問A99がYesのとき注意とするルールを追加して");
  const error = await screen.findByTestId("proposal-error");
  expect(error.textContent).toContain("検証で拒否されました");
  expect(error.textContent).toContain("設問A99は存在しません");
  expect(error.textContent).toContain("判定ルールの一覧は変わっていません");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(rowsOf(current())).toBe(3);
});

test("検証エラー（重複）: 重複するルールの理由を、そのまま出す", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/rule-proposals": { status: 422, body: { detail: "条件と結果が同じルールR01が既に存在します（重複）" } },
  });
  render(<RuleAdmin />);
  await createProposal();
  expect((await screen.findByTestId("proposal-error")).textContent).toContain("ルールR01が既に存在します（重複）");
});

test("AIの応答を得られない（502）: 成功に見せず、一覧は変わっていないと伝える", async () => {
  stubFetch({ ...baseRoutes(), "POST /api/admin/rule-proposals": { status: 502, body: { detail: "AIの応答を得られませんでした（モック）" } } });
  render(<RuleAdmin />);
  await createProposal();
  const text = (await screen.findByTestId("proposal-error")).textContent ?? "";
  expect(text).toContain("AIの応答を得られませんでした");
  expect(text).toContain("判定ルールの一覧は変わっていません");
  expect(screen.queryByTestId("proposal-notice")).toBeNull();
});

test("競合（409）: 反映しなかったと伝え、プレビューを閉じて、サーバーの最新の一覧を取り直す", async () => {
  let listCalls = 0;
  stubFetch({
    "GET /api/admin/rules": () => {
      listCalls += 1;
      return { body: listCalls === 1 ? listBody() : listBody([...baseRules, rule("R04", "no_issue", { A05: "yes" })], 2) };
    },
    "POST /api/admin/rule-proposals": { status: 201, body: addBody },
    "POST /api/admin/rule-proposals/p1/decision": { status: 409, body: { detail: "判定ルールの一覧が提案の後に変わったため、反映しませんでした" } },
  });
  render(<RuleAdmin />);
  await createProposal();
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  const error = await screen.findByTestId("proposal-error");
  expect(error.textContent).toContain("反映しませんでした");
  expect(error.textContent).toContain("最新の一覧を表示しています");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(screen.queryByTestId("proposal-notice")).toBeNull();
  await waitFor(() => expect(rowsOf(current())).toBe(4)); // 最新（他の人が追加したR04）
});

test("決定の通信エラーは、反映の有無を言い切らない。プレビューは残る", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/rule-proposals": { status: 201, body: addBody },
    "POST /api/admin/rule-proposals/p1/decision": new Error("network down"),
  });
  render(<RuleAdmin />);
  await createProposal();
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));
  const text = (await screen.findByTestId("proposal-error")).textContent ?? "";
  expect(text).toContain("確認できていません");
  expect(text).not.toContain("変わっていません");
  expect(screen.getByTestId("proposal")).toBeTruthy();
});

test("決定の送信中は、Yes・Noを押せず、二重に送らない", async () => {
  let release: (r: Response) => void = () => {};
  const decisionCalls: RequestInit[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      if (key === "GET /api/admin/rules") return new Response(JSON.stringify(listBody()));
      if (key === "POST /api/admin/rule-proposals") return new Response(JSON.stringify(addBody), { status: 201 });
      if (key === "POST /api/admin/rule-proposals/p1/decision") {
        decisionCalls.push(init ?? {});
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }
      throw new Error(`想定外のリクエスト: ${key}`);
    }),
  );
  render(<RuleAdmin />);
  await createProposal();
  await screen.findByTestId("proposal");
  const yes = screen.getByRole("button", { name: "Yes" }) as HTMLButtonElement;
  const no = screen.getByRole("button", { name: "No" }) as HTMLButtonElement;
  fireEvent.click(yes);
  await waitFor(() => expect(yes.disabled).toBe(true));
  expect(no.disabled).toBe(true);
  fireEvent.click(yes);
  fireEvent.click(no);
  expect(decisionCalls).toHaveLength(1);
  release(new Response(JSON.stringify({ applied: true, ...listBody(afterRules, 2) })));
  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(rowsOf(current())).toBe(4);
});

test("一覧を取得できないときは、エラーを出す", async () => {
  stubFetch({ "GET /api/admin/rules": { status: 500, body: {} } });
  render(<RuleAdmin />);
  expect((await screen.findByTestId("list-error")).textContent).toContain("デモの判定ルール一覧を取得できませんでした");
});

test("未知・使用済み（404）: 反映していないと伝え、プレビューを閉じて、最新の一覧を取り直す", async () => {
  let listCalls = 0;
  stubFetch({
    "GET /api/admin/rules": () => {
      listCalls += 1;
      return { body: listCalls === 1 ? listBody() : listBody([...baseRules, rule("R04", "no_issue", { A05: "yes" })], 2) };
    },
    "POST /api/admin/rule-proposals": { status: 201, body: addBody },
    "POST /api/admin/rule-proposals/p1/decision": { status: 404, body: { detail: "提案がありません（未知、または処理済みです）" } },
  });
  render(<RuleAdmin />);
  await createProposal();
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  expect((await screen.findByTestId("proposal-error")).textContent).toContain("提案がありません");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(screen.queryByTestId("proposal-notice")).toBeNull();
  await waitFor(() => expect(rowsOf(current())).toBe(4));
  expect(listCalls).toBe(2);
  // 閉じたあとは、同じ案を送れない。
  expect(screen.queryByRole("button", { name: "Yes" })).toBeNull();
});

test("削除案のYes: 削除の表示と、最新の一覧（削除後の件数）を出して、プレビューを閉じる", async () => {
  const spy = stubFetch({
    ...baseRoutes(),
    "POST /api/admin/rule-proposals": { status: 201, body: removeBody },
    "POST /api/admin/rule-proposals/p2/decision": { body: { applied: true, ...listBody(baseRules.slice(0, 2), 2) } },
  });
  render(<RuleAdmin />);
  await createProposal("ルールR03を削除して");
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(screen.getByTestId("proposal-notice").textContent).toBe("デモの判定ルール一覧に反映しました（ルールR03を削除）");
  expect(rowsOf(current())).toBe(2);
  expect(within(current()).queryByText("R03")).toBeNull();
  const decision = spy.mock.calls.find(([url]) => String(url).endsWith("/decision"));
  expect(JSON.parse(String(decision?.[1]?.body))).toEqual({ decision: "yes" });
});

test("削除案のNo: 「この削除案は反映していません」と出し、現行の一覧（3件）は変わらない", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/rule-proposals": { status: 201, body: removeBody },
    "POST /api/admin/rule-proposals/p2/decision": { body: { applied: false, ...listBody() } },
  });
  render(<RuleAdmin />);
  await createProposal("ルールR03を削除して");
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "No" }));

  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(screen.getByTestId("proposal-notice").textContent).toContain("この削除案は反映していません");
  expect(screen.queryByText(/に反映しました/)).toBeNull();
  expect(rowsOf(current())).toBe(3);
  expect(within(current()).getByText("R03")).toBeTruthy();
});

test.each([
  [404, "提案がありません（未知、または処理済みです）", "提案がありません"],
  [409, "判定ルールの一覧が提案の後に変わったため、反映しませんでした", "最新の一覧を表示しています"],
])("削除案のYesが%i: 反映したとは言わず、プレビューを閉じて最新の一覧を取り直す。「追加案」とは案内しない", async (status, detail, expected) => {
  let listCalls = 0;
  stubFetch({
    "GET /api/admin/rules": () => {
      listCalls += 1;
      return { body: listCalls === 1 ? listBody() : listBody(baseRules.slice(0, 2), 2) }; // 他の人がR03を削除した後
    },
    "POST /api/admin/rule-proposals": { status: 201, body: removeBody },
    "POST /api/admin/rule-proposals/p2/decision": { status, body: { detail } },
  });
  render(<RuleAdmin />);
  await createProposal("ルールR03を削除して");
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  const text = (await screen.findByTestId("proposal-error")).textContent ?? "";
  expect(text).toContain(expected);
  expect(text).not.toContain("追加案");
  if (status === 404) expect(text).toContain("もう一度、案を作ってください");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(screen.queryByTestId("proposal-notice")).toBeNull();
  expect(screen.queryByText(/に反映しました/)).toBeNull();
  await waitFor(() => expect(rowsOf(current())).toBe(2));
  expect(listCalls).toBe(2);
});

test("案の作成中は、［案を作る］を押せず、1回しか送らない", async () => {
  let release: (r: Response) => void = () => {};
  const createCalls: RequestInit[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      if (key === "GET /api/admin/rules") return new Response(JSON.stringify(listBody()));
      if (key === "POST /api/admin/rule-proposals") {
        createCalls.push(init ?? {});
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }
      throw new Error(`想定外のリクエスト: ${key}`);
    }),
  );
  render(<RuleAdmin />);
  await createProposal();
  const button = screen.getByRole("button", { name: "案を作る" }) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(true));
  fireEvent.click(button);
  fireEvent.submit(screen.getByLabelText("変更の依頼（自然文）").closest("form") as HTMLFormElement);
  expect(createCalls).toHaveLength(1);
  release(new Response(JSON.stringify(addBody), { status: 201 }));
  await screen.findByTestId("proposal");
  expect(button.disabled).toBe(false);
  expect(createCalls).toHaveLength(1);
});

test("2案目の作成が検証エラー（422）になると、画面上の前の案は閉じる。決定は送らず、一覧は変わらない", async () => {
  let posts = 0;
  const spy = stubFetch({
    ...baseRoutes(),
    "POST /api/admin/rule-proposals": () => {
      posts += 1;
      return posts === 1 ? { status: 201, body: addBody } : { status: 422, body: { detail: "設問A99は存在しません（ルールは参照できません）" } };
    },
  });
  render(<RuleAdmin />);
  await createProposal();
  await screen.findByTestId("proposal");

  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: "設問A99がYesのとき注意とするルールを追加して" } });
  fireEvent.click(screen.getByRole("button", { name: "案を作る" }));
  expect((await screen.findByTestId("proposal-error")).textContent).toContain("設問A99は存在しません");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(screen.queryByRole("button", { name: "Yes" })).toBeNull();
  expect(rowsOf(current())).toBe(3);
  expect(spy.mock.calls.some(([url]) => String(url).endsWith("/decision"))).toBe(false);
});

test("ルールが0件の一覧は、見出しと0件を出し、エラーにしない。追加案も作れる", async () => {
  stubFetch({
    "GET /api/admin/rules": { body: listBody([]) },
    "POST /api/admin/rule-proposals": { status: 201, body: { ...addBody, before: [], after: [added] } },
  });
  render(<RuleAdmin />);
  const region = await screen.findByRole("region", { name: "現行のデモの判定ルール一覧" });
  expect(within(region).getByText("0件")).toBeTruthy();
  expect(rowsOf(region)).toBe(0);
  expect(screen.queryByTestId("list-error")).toBeNull();

  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: PROMPT } });
  fireEvent.click(screen.getByRole("button", { name: "案を作る" }));
  const proposal = await screen.findByTestId("proposal");
  expect(rowsOf(within(proposal).getByRole("region", { name: "Before（現行）" }))).toBe(0);
  expect(rowsOf(within(proposal).getByRole("region", { name: "After（反映した場合）" }))).toBe(1);
});
