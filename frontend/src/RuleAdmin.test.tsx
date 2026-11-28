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
const PROMPT = "Add a rule that gives Caution when A09 is Yes and A10 is No";

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

const current = () => screen.getByRole("region", { name: "Current demo rule list" });
const rowsOf = (region: HTMLElement) => within(region).getAllByRole("row").length - 1; // 見出しの行を除く

async function createProposal(prompt = PROMPT) {
  await screen.findByText("R03");
  fireEvent.change(screen.getByLabelText("Change request (plain language)"), { target: { value: prompt } });
  fireEvent.click(screen.getByRole("button", { name: "Create proposal" }));
}

test("操作の対象が判定ルールであることと、モック・保存の範囲・審査画面に影響しないことを出す", async () => {
  render(<RuleAdmin />);
  expect(screen.getByRole("heading", { name: "Rule admin (demo)" })).toBeTruthy();
  expect(screen.getByTestId("target").textContent).toBe("Editing: Judgment rules");
  await screen.findByText("R03"); // 生成元は、一覧の応答で分かる
  const note = screen.getByTestId("mock-note").textContent ?? "";
  expect(note).toContain("This is a mock");
  expect(note).toContain("returns to its initial state when the backend restarts");
  expect(note).toContain("The rules used for matching on the review screen, the cases, and the database do not change");
  await screen.findByText("R03");
});

test("現行のデモのルール一覧を、ルールID・版・結果の表示名・条件（設問＝回答）で出す。プレビューはまだない", async () => {
  render(<RuleAdmin />);
  await screen.findByText("R03");
  expect(rowsOf(current())).toBe(3);
  const r1 = within(current()).getByTestId("Current demo rule list-R01");
  expect(r1.textContent).toContain("Caution");
  expect(within(current()).getByTestId("Current demo rule list-R03").textContent).toContain("Account suspension");
  expect(screen.queryByTestId("proposal")).toBeNull();
});

const headers = (region: HTMLElement) => within(region).getAllByRole("columnheader").map((h) => h.textContent);
const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((c) => c.textContent);
const FIXED = Array.from({ length: 12 }, (_, i) => `A${String(i + 1).padStart(2, "0")}`);
const dash = (n: number) => Array<string>(n).fill("-");

test("現行の一覧は、ルール・版・結果の後にA01〜A12を固定の12列で並べ、条件をYes／No、条件なしを - で出す", async () => {
  render(<RuleAdmin />);
  await screen.findByText("R03");
  expect(headers(current())).toEqual(["Rule", "Revision", "Result", ...FIXED]);
  const row = (id: string) => within(current()).getByTestId(`Current demo rule list-${id}`);
  expect(cells(row("R01"))).toEqual(["1", "Caution", "Yes", "No", ...dash(10)]);
  expect(cells(row("R02"))).toEqual(["1", "No issue", "-", "-", "Yes", ...dash(9)]);
  expect(cells(row("R03"))).toEqual(["1", "Account suspension", "-", "-", "-", "No", ...dash(8)]);
  // Yes／Noは操作できないラベル（ボタンではない）。条件なしの - はラベルにしない。
  const tds = row("R01").querySelectorAll("td");
  expect(tds[2].querySelector(".answer-chip.answer-yes")?.textContent).toBe("Yes");
  expect(tds[3].querySelector(".answer-chip.answer-no")?.textContent).toBe("No");
  expect(tds[4].querySelector(".answer-chip")).toBeNull();
  expect(within(row("R01")).queryAllByRole("button")).toHaveLength(0);
  // 横スクロールは、フォーカスできる現行表のラッパーの中だけ。
  const wrap = within(current()).getByRole("group", { name: "Current demo rule list table (scrolls horizontally)" });
  expect(wrap.getAttribute("tabindex")).toBe("0");
  expect(wrap.querySelector("table")).not.toBeNull();
});

test("A13以降の条件を持つルールがあるときだけ、A12の後ろに自然順で列を足す", async () => {
  const rules = [rule("R01", "caution", { A13: "yes", A02: "no" }), rule("R02", "no_issue", { A100: "no", A13: "no" })];
  stubFetch({ "GET /api/admin/rules": { body: listBody(rules) } });
  render(<RuleAdmin />);
  await screen.findByText("R02");
  expect(headers(current())).toEqual(["Rule", "Revision", "Result", ...FIXED, "A13", "A100"]);
  const row = (id: string) => within(current()).getByTestId(`Current demo rule list-${id}`);
  expect(cells(row("R01"))).toEqual(["1", "Caution", "-", "No", ...dash(10), "Yes", "-"]);
  expect(cells(row("R02"))).toEqual(["1", "No issue", ...dash(12), "No", "No"]);
});

test("Before／Afterの表は、これまでどおり1列の条件（設問＝回答）で出す", async () => {
  stubFetch({ ...baseRoutes(), "POST /api/admin/rule-proposals": { status: 201, body: addBody } });
  render(<RuleAdmin />);
  await createProposal();
  const after = await screen.findByRole("region", { name: "After (if applied)" });
  expect(headers(after)).toEqual(["Rule", "Revision", "Result", "Conditions (question=answer)"]);
});

test("依頼が空のあいだは、［案を作る］を押せない", async () => {
  render(<RuleAdmin />);
  await screen.findByText("R03");
  const button = screen.getByRole("button", { name: "Create proposal" }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Change request (plain language)"), { target: { value: "  " } });
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Change request (plain language)"), { target: { value: PROMPT } });
  expect(button.disabled).toBe(false);
});

test("追加案: Before／Afterの全一覧を出し、追加行を「追加」ラベルと背景で示す。現行の一覧は変わらず、決定も送らない", async () => {
  const spy = stubFetch({ ...baseRoutes(), "POST /api/admin/rule-proposals": { status: 201, body: addBody } });
  render(<RuleAdmin />);
  await createProposal();
  const proposal = await screen.findByTestId("proposal");

  expect(within(proposal).getByRole("heading", { name: /Proposed change: add rule/ })).toBeTruthy();
  const before = within(proposal).getByRole("region", { name: "Before (current)" });
  const after = within(proposal).getByRole("region", { name: "After (if applied)" });
  expect(rowsOf(before)).toBe(3);
  expect(rowsOf(after)).toBe(4);
  const row = within(after).getByTestId("After (if applied)-R04");
  expect(row.className).toContain("row-added");
  expect(within(row).getByText("Added")).toBeTruthy();
  expect(row.textContent).toContain("A09=Yes");
  expect(row.textContent).toContain("A10=No");
  expect(within(after).getByTestId("After (if applied)-R03").className).not.toContain("row-added");
  expect(screen.getByText("Apply this change?")).toBeTruthy();

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
  await createProposal("Remove rule R03");
  const proposal = await screen.findByTestId("proposal");

  expect(within(proposal).getByRole("heading", { name: /Proposed change: remove rule/ })).toBeTruthy();
  const before = within(proposal).getByRole("region", { name: "Before (current)" });
  const after = within(proposal).getByRole("region", { name: "After (if applied)" });
  const row = within(before).getByTestId("Before (current)-R03");
  expect(row.className).toContain("row-retired");
  expect(within(row).getByText("Removed")).toBeTruthy();
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
  expect(screen.getByTestId("proposal-notice").textContent).toBe("Applied to the demo rule list (rule R04 added)");
  expect(rowsOf(current())).toBe(4);
  expect(within(current()).getByTestId("Current demo rule list-R04")).toBeTruthy();
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
  expect(screen.getByTestId("proposal-notice").textContent).toContain("This add proposal was not applied");
  expect(screen.queryByText(/Applied to/)).toBeNull();
  expect(rowsOf(current())).toBe(3);
  const decision = spy.mock.calls.find(([url]) => String(url).endsWith("/decision"));
  expect(JSON.parse(String(decision?.[1]?.body))).toEqual({ decision: "no" });
});

test("検証エラー（422）: 理由を出し、プレビューは出さず、現行の一覧は変わらない", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/rule-proposals": { status: 422, body: { detail: "Question A99 does not exist (a rule cannot reference nonexistent or deactivated questions)" } },
  });
  render(<RuleAdmin />);
  await createProposal("Add a rule that gives Caution when A99 is Yes");
  const error = await screen.findByTestId("proposal-error");
  expect(error.textContent).toContain("rejected by validation");
  expect(error.textContent).toContain("Question A99 does not exist");
  expect(error.textContent).toContain("The judgment rule list has not changed");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(rowsOf(current())).toBe(3);
});

test("検証エラー（重複）: 重複するルールの理由を、そのまま出す", async () => {
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/rule-proposals": { status: 422, body: { detail: "条件と結果が同じRule R01 already exists (duplicate)" } },
  });
  render(<RuleAdmin />);
  await createProposal();
  expect((await screen.findByTestId("proposal-error")).textContent).toContain("Rule R01 already exists (duplicate)");
});

test("AIの応答を得られない（502）: 成功に見せず、一覧は変わっていないと伝える", async () => {
  stubFetch({ ...baseRoutes(), "POST /api/admin/rule-proposals": { status: 502, body: { detail: "Could not get an AI response (mock)" } } });
  render(<RuleAdmin />);
  await createProposal();
  const text = (await screen.findByTestId("proposal-error")).textContent ?? "";
  expect(text).toContain("Could not get an AI response");
  expect(text).toContain("The judgment rule list has not changed");
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
    "POST /api/admin/rule-proposals/p1/decision": { status: 409, body: { detail: "判定ルールの一覧が提案の後に変わったため、nothing was applied" } },
  });
  render(<RuleAdmin />);
  await createProposal();
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  const error = await screen.findByTestId("proposal-error");
  expect(error.textContent).toContain("nothing was applied");
  expect(error.textContent).toContain("Showing the latest list");
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
  expect(text).toContain("It is unknown whether");
  expect(text).not.toContain("has not changed");
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
      throw new Error(`Unexpected request: ${key}`);
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
  expect((await screen.findByTestId("list-error")).textContent).toContain("Could not retrieve the demo rule list");
});

test("未知・使用済み（404）: 反映していないと伝え、プレビューを閉じて、最新の一覧を取り直す", async () => {
  let listCalls = 0;
  stubFetch({
    "GET /api/admin/rules": () => {
      listCalls += 1;
      return { body: listCalls === 1 ? listBody() : listBody([...baseRules, rule("R04", "no_issue", { A05: "yes" })], 2) };
    },
    "POST /api/admin/rule-proposals": { status: 201, body: addBody },
    "POST /api/admin/rule-proposals/p1/decision": { status: 404, body: { detail: "Proposal not found（未知、または処理済みです）" } },
  });
  render(<RuleAdmin />);
  await createProposal();
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  expect((await screen.findByTestId("proposal-error")).textContent).toContain("Proposal not found");
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
  await createProposal("Remove rule R03");
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(screen.getByTestId("proposal-notice").textContent).toBe("Applied to the demo rule list (rule R03 removed)");
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
  await createProposal("Remove rule R03");
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "No" }));

  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(screen.getByTestId("proposal-notice").textContent).toContain("This remove proposal was not applied");
  expect(screen.queryByText(/Applied to/)).toBeNull();
  expect(rowsOf(current())).toBe(3);
  expect(within(current()).getByText("R03")).toBeTruthy();
});

test.each([
  [404, "Proposal not found（未知、または処理済みです）", "Proposal not found"],
  [409, "判定ルールの一覧が提案の後に変わったため、nothing was applied", "Showing the latest list"],
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
  await createProposal("Remove rule R03");
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  const text = (await screen.findByTestId("proposal-error")).textContent ?? "";
  expect(text).toContain(expected);
  expect(text).not.toContain("add proposal");
  if (status === 404) expect(text).toContain("Please create the proposal again");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(screen.queryByTestId("proposal-notice")).toBeNull();
  expect(screen.queryByText(/Applied to/)).toBeNull();
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
      throw new Error(`Unexpected request: ${key}`);
    }),
  );
  render(<RuleAdmin />);
  await createProposal();
  const button = screen.getByRole("button", { name: "Create proposal" }) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(true));
  fireEvent.click(button);
  fireEvent.submit(screen.getByLabelText("Change request (plain language)").closest("form") as HTMLFormElement);
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
      return posts === 1 ? { status: 201, body: addBody } : { status: 422, body: { detail: "Question A99 does not exist (a rule cannot reference nonexistent or deactivated questions)" } };
    },
  });
  render(<RuleAdmin />);
  await createProposal();
  await screen.findByTestId("proposal");

  fireEvent.change(screen.getByLabelText("Change request (plain language)"), { target: { value: "Add a rule that gives Caution when A99 is Yes" } });
  fireEvent.click(screen.getByRole("button", { name: "Create proposal" }));
  expect((await screen.findByTestId("proposal-error")).textContent).toContain("Question A99 does not exist");
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
  const region = await screen.findByRole("region", { name: "Current demo rule list" });
  expect(within(region).getByText("0 rules")).toBeTruthy();
  expect(rowsOf(region)).toBe(0);
  expect(screen.queryByTestId("list-error")).toBeNull();

  fireEvent.change(screen.getByLabelText("Change request (plain language)"), { target: { value: PROMPT } });
  fireEvent.click(screen.getByRole("button", { name: "Create proposal" }));
  const proposal = await screen.findByTestId("proposal");
  expect(rowsOf(within(proposal).getByRole("region", { name: "Before (current)" }))).toBe(0);
  expect(rowsOf(within(proposal).getByRole("region", { name: "After (if applied)" }))).toBe(1);
});


test("接続できない・503は同じ依頼ID、502・504・429は新しい依頼IDで再送する", async () => {
  const run = async (reply: Error | { status: number; body: unknown }) => {
    const keys: Array<string | null> = [];
    stubFetch({
      ...baseRoutes(),
      "POST /api/admin/rule-proposals": (init) => {
        keys.push(new Headers(init?.headers).get("Idempotency-Key"));
        return keys.length === 1 ? reply : { status: 201, body: addBody };
      },
    });
    render(<RuleAdmin />);
    await screen.findByText("R01");
    fireEvent.change(screen.getByLabelText("Change request (plain language)"), { target: { value: PROMPT } });
    fireEvent.click(screen.getByRole("button", { name: "Create proposal" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Create proposal" }));
    await waitFor(() => expect(keys).toHaveLength(2));
    cleanup();
    return keys;
  };
  for (const reply of [new Error("offline"), { status: 503, body: { detail: "busy" } }]) {
    const keys = await run(reply);
    expect(keys[1]).toBe(keys[0]);
  }
  for (const status of [502, 504, 429]) {
    const keys = await run({ status, body: { detail: "理由" } });
    expect(keys[1], String(status)).not.toBe(keys[0]);
  }
});

test("定型案: 生成元を示し、Noでは再提案せず、「AIで再提案」だけが元の依頼でforce_aiを送る", async () => {
  const sent: unknown[] = [];
  stubFetch({
    ...baseRoutes(),
    "POST /api/admin/rule-proposals": (init) => {
      const body = JSON.parse(init?.body as string);
      sent.push(body);
      return { status: 201, body: { ...removeBody, source: { kind: body.force_ai ? "bedrock" : "template" } } };
    },
    "POST /api/admin/rule-proposals/p2/decision": { body: { ...listBody(), applied: false } },
  });
  render(<RuleAdmin />);
  await createProposal("Remove rule R03");
  await screen.findByTestId("proposal");
  expect(screen.getByTestId("source-badge").getAttribute("data-source")).toBe("template");
  fireEvent.click(screen.getByRole("button", { name: "No", exact: true }));
  const retry = await screen.findByRole("button", { name: "Re-propose with AI" });
  expect(sent).toEqual([{ prompt: "Remove rule R03" }]); // Noだけでは、再提案の要求を送らない
  fireEvent.change(screen.getByLabelText("Change request (plain language)"), { target: { value: "入力欄を書き換えた" } });
  fireEvent.click(retry);
  await screen.findByTestId("proposal");
  expect(sent).toEqual([{ prompt: "Remove rule R03" }, { prompt: "Remove rule R03", force_ai: true }]);
  expect(screen.getByTestId("source-badge").getAttribute("data-source")).toBe("bedrock");
  expect(screen.queryByRole("button", { name: "Re-propose with AI" })).toBeNull();
});

const TEMPLATE_HEADING = "Saved requests (exact match, no AI call)";
const TEMPLATE_NOTE = "If an unintended change proposal appears, delete the matching request.";

test("定型文の一覧は初期状態で閉じ、指定の見出しと案内を出す。登録の操作はない", async () => {
  stubFetch({
    "GET /api/admin/rules": { body: { ...listBody(), template_reuse: true } },
    "GET /api/admin/proposal-templates/rule": { body: { prompts: ["Remove rule R03"] } },
  });
  render(<RuleAdmin />);
  const summary = await screen.findByText(TEMPLATE_HEADING);
  const details = summary.closest("details")!;
  expect(summary.tagName).toBe("SUMMARY");
  expect(details.open).toBe(false);
  expect(within(details).getByText(TEMPLATE_NOTE).closest("small")).not.toBeNull();
  await screen.findByRole("button", { name: "Delete Remove rule R03 from saved requests" });
  expect(screen.queryByRole("button", { name: "再利用に登録" })).toBeNull();
});

test("Bedrock案のYesで、自動登録された定型文の一覧を取得し直す。登録の要求は画面から送らない", async () => {
  const templatePath = "/api/admin/proposal-templates/rule";
  let templateReads = 0;
  const spy = stubFetch({
    "GET /api/admin/rules": { body: { ...listBody(), template_reuse: true } },
    ["GET " + templatePath]: () => {
      templateReads += 1;
      return { body: { prompts: templateReads === 1 ? [] : [PROMPT] } };
    },
    "POST /api/admin/rule-proposals": { status: 201, body: { ...addBody, source: { kind: "bedrock", model_id: "m", region: "r" } } },
    "POST /api/admin/rule-proposals/p1/decision": { body: { ...listBody(afterRules, 2), applied: true, template_reuse: true } },
  });
  render(<RuleAdmin />);
  await createProposal();
  await screen.findByTestId("proposal");
  await waitFor(() => expect(templateReads).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "Yes", exact: true }));
  await screen.findByRole("button", { name: `Delete ${PROMPT} from saved requests` });
  expect(templateReads).toBe(2);
  expect(spy.mock.calls.some(([url, init]) => url === templatePath && init?.method === "POST")).toBe(false);
});

test("Noを選んだBedrock案では、定型文の一覧を取得し直さず、「AIで再提案」も出ない", async () => {
  const spy = stubFetch({
    "GET /api/admin/rules": { body: { ...listBody(), template_reuse: true } },
    "GET /api/admin/proposal-templates/rule": { body: { prompts: [] } },
    "POST /api/admin/rule-proposals": { status: 201, body: { ...addBody, source: { kind: "bedrock", model_id: "m", region: "r" } } },
    "POST /api/admin/rule-proposals/p1/decision": { body: { ...listBody(), applied: false, template_reuse: true } },
  });
  render(<RuleAdmin />);
  await createProposal();
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "No", exact: true }));
  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(spy.mock.calls.filter(([url]) => url === "/api/admin/proposal-templates/rule")).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Re-propose with AI" })).toBeNull();
});
