// 実行: npm test（Vitest + jsdom）。判定ルールの条件の更新（既存ルールへの条件の追加）の画面の流れを確認する。
// APIはモックで、サーバーには接続しない。R04は、この画面のテストの準備（架空の既存ルール）。
// 更新の依頼「R04にA09がYesを追加して」で、R04のID・既存の条件・結果を保ち、R05を新設しない差分を出す。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import RuleAdmin from "./RuleAdmin";
import { stubFetch, type Routes } from "./testing";

type Cond = Record<string, "yes" | "no">;
const rule = (rule_id: string, result_code: string, conditions: Cond, revision = 1) => ({ rule_id, revision, result_code, conditions });
const r04Before = rule("R04", "caution", { A10: "no" });
const r04After = rule("R04", "caution", { A09: "yes", A10: "no" }, 2);
const baseRules = [
  rule("R01", "caution", { A01: "yes", A02: "no" }),
  rule("R02", "no_issue", { A03: "yes" }),
  rule("R03", "account_suspension", { A04: "no" }),
  r04Before,
];
const updatedRules = [...baseRules.slice(0, 3), r04After];
const PROMPT = "R04にA09がYesを追加して";

const listBody = (rules = baseRules, revision = 2) => ({ revision, rules, mock: true, persistence: "memory", source: { kind: "mock" } });
const updateBody = {
  proposal_id: "u1",
  kind: "update",
  base_revision: 2,
  before: baseRules,
  after: updatedRules,
  target: r04Before,
  updated: r04After,
  added_conditions: { A09: "yes" },
  mock: true,
  source: { kind: "mock" },
};
const baseRoutes = (): Routes => ({ "GET /api/admin/rules": { body: listBody() } });
const PROPOSALS = "POST /api/admin/rule-proposals";
const DECISION = "POST /api/admin/rule-proposals/u1/decision";

beforeEach(() => {
  stubFetch(baseRoutes());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const current = () => screen.getByRole("region", { name: "現行のデモの判定ルール一覧" });
const rowsOf = (region: HTMLElement) => within(region).getAllByRole("row").length - 1; // 見出しの行を除く
const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((c) => c.textContent);
const currentRow = (id: string) => within(current()).getByTestId(`現行のデモの判定ルール一覧-${id}`);

async function requestUpdate(prompt = PROMPT) {
  await screen.findByText("R04");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: prompt } });
  fireEvent.click(screen.getByRole("button", { name: "案を作る" }));
}

test("更新の依頼の例を、依頼欄の近くに出す", async () => {
  render(<RuleAdmin />);
  await screen.findByText("R04");
  expect(screen.getByText(/R04にA09がYesを追加して/)).toBeTruthy();
});

test("更新案: R04を対象に、追加する条件A09=Yesを差分で示す。R05は出ず、現行の一覧は変わらない", async () => {
  const spy = stubFetch({ ...baseRoutes(), [PROPOSALS]: { status: 201, body: updateBody } });
  render(<RuleAdmin />);
  await requestUpdate();
  const proposal = await screen.findByTestId("proposal");

  expect(within(proposal).getByRole("heading", { name: /判定ルールの更新案の確認（まだ反映していません）/ })).toBeTruthy();
  const summary = within(proposal).getByTestId("update-summary").textContent ?? "";
  expect(summary).toContain("更新の対象: ルールR04");
  expect(summary).toContain("追加する条件: A09=Yes");

  const before = within(proposal).getByRole("region", { name: "Before（現行）" });
  const after = within(proposal).getByRole("region", { name: "After（反映した場合）" });
  expect(rowsOf(before)).toBe(4);
  expect(rowsOf(after)).toBe(4); // ルールは増えない
  expect(within(after).queryByText("R05")).toBeNull();

  const beforeRow = within(before).getByTestId("Before（現行）-R04");
  const afterRow = within(after).getByTestId("After（反映した場合）-R04");
  for (const row of [beforeRow, afterRow]) {
    expect(row.className).toContain("row-updated");
    expect(within(row).getByText("更新")).toBeTruthy();
    expect(row.className).not.toContain("row-added");
  }
  // Before: 既存の条件だけ。After: 既存の条件（A10=No）を保ち、A09=Yesが「追加」と分かる。結果と版は変えない列を並べて見せる。
  expect(beforeRow.textContent).toContain("A10=No");
  expect(beforeRow.textContent).not.toContain("A09");
  expect(afterRow.textContent).toContain("A10=No");
  const added = afterRow.querySelectorAll("[data-added='true']");
  expect(added).toHaveLength(1);
  expect(added[0].textContent).toContain("A09=Yes");
  expect(added[0].textContent).toContain("（追加）");
  expect(afterRow.textContent).toContain("注意");
  expect(afterRow.querySelector("[data-added='true']")?.textContent).not.toContain("A10");
  // 他のルールは更新の対象として示さない。
  expect(within(after).getByTestId("After（反映した場合）-R03").className).not.toContain("row-updated");
  expect(within(after).getByTestId("After（反映した場合）-R03").querySelector("[data-added]")).toBeNull();
  expect(screen.getByText("反映しますか？")).toBeTruthy();

  // 現行の一覧は変わらない。決定も送っていない。
  expect(rowsOf(current())).toBe(4);
  expect(cells(currentRow("R04"))[0]).toBe("1");
  const posts = spy.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(posts).toHaveLength(1);
  expect(JSON.parse(String(posts[0][1]?.body))).toEqual({ prompt: PROMPT });
});

test("Yes: 決定にyesを送り、R04だけに条件が追加された一覧を出す。ルールの数は同じで、R05は作られない", async () => {
  const spy = stubFetch({
    ...baseRoutes(),
    [PROPOSALS]: { status: 201, body: updateBody },
    [DECISION]: { body: { applied: true, ...listBody(updatedRules, 3) } },
  });
  render(<RuleAdmin />);
  await requestUpdate();
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(screen.getByTestId("proposal-notice").textContent).toBe("デモの判定ルール一覧に反映しました（ルールR04の条件を更新）");
  expect(rowsOf(current())).toBe(4);
  expect(within(current()).queryByText("R05")).toBeNull();
  // R04: 版2、A09=Yes（追加）とA10=No（既存）。結果は変わらない。
  expect(cells(currentRow("R04"))).toEqual(["2", "注意", ...Array<string>(8).fill("-"), "Yes", "No", "-", "-"]);
  // 他のルールは変わらない（版1のまま）。
  expect(cells(currentRow("R01"))).toEqual(["1", "注意", "Yes", "No", ...Array<string>(10).fill("-")]);
  expect(cells(currentRow("R03"))).toEqual(["1", "アカウント一時凍結", "-", "-", "-", "No", ...Array<string>(8).fill("-")]);
  const decision = spy.mock.calls.find(([url]) => String(url).endsWith("/decision"));
  expect(JSON.parse(String(decision?.[1]?.body))).toEqual({ decision: "yes" });
});

test("No: 承認済みの一覧（R04は元の条件）のまま。決定にnoを送り、「反映した」とは言わない", async () => {
  const spy = stubFetch({
    ...baseRoutes(),
    [PROPOSALS]: { status: 201, body: updateBody },
    [DECISION]: { body: { applied: false, ...listBody() } },
  });
  render(<RuleAdmin />);
  await requestUpdate();
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "No" }));

  await waitFor(() => expect(screen.queryByTestId("proposal")).toBeNull());
  expect(screen.getByTestId("proposal-notice").textContent).toContain("この更新案は反映していません");
  expect(screen.queryByText(/に反映しました/)).toBeNull();
  expect(rowsOf(current())).toBe(4);
  expect(cells(currentRow("R04"))).toEqual(["1", "注意", ...Array<string>(9).fill("-"), "No", "-", "-"]);
  const decision = spy.mock.calls.find(([url]) => String(url).endsWith("/decision"));
  expect(JSON.parse(String(decision?.[1]?.body))).toEqual({ decision: "no" });
});

test.each([
  ["対象のルールがない", "ルールID R09 は存在しません"],
  ["設問がない", "設問A99は存在しません（ルールは参照できません）"],
  ["指定が曖昧", "依頼に複数のルール（R01、R04）があり、更新の対象が曖昧です。1つのルールを指定してください"],
  ["同じ条件を追加済み", "ルールR04には、設問A09=yesの条件が追加済みです（重複）"],
  ["新規作成として返された", "ルールR04の条件を更新する依頼ですが、更新ではない案が返りました。新しいルールの追加などには置き換えず、反映できません"],
])("拒否（422・%s）: 理由を出し、プレビューは出さず、現行の一覧は変わらない", async (_name, detail) => {
  stubFetch({ ...baseRoutes(), [PROPOSALS]: { status: 422, body: { detail } } });
  render(<RuleAdmin />);
  await requestUpdate();
  const error = await screen.findByTestId("proposal-error");
  expect(error.textContent).toContain("検証で拒否されました");
  expect(error.textContent).toContain(detail);
  expect(error.textContent).toContain("判定ルールの一覧は変わっていません");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(screen.queryByTestId("proposal-notice")).toBeNull();
  expect(rowsOf(current())).toBe(4);
  expect(cells(currentRow("R04"))[0]).toBe("1");
});

test("版の競合（409）: 反映しなかったと伝え、プレビューを閉じ、サーバーの最新の一覧（他の人の更新後）を取り直す", async () => {
  let listCalls = 0;
  stubFetch({
    "GET /api/admin/rules": () => {
      listCalls += 1;
      return { body: listCalls === 1 ? listBody() : listBody(updatedRules, 3) };
    },
    [PROPOSALS]: { status: 201, body: updateBody },
    [DECISION]: { status: 409, body: { detail: "判定ルールの一覧が提案の後に変わったため、反映しませんでした" } },
  });
  render(<RuleAdmin />);
  await requestUpdate();
  await screen.findByTestId("proposal");
  fireEvent.click(screen.getByRole("button", { name: "Yes" }));

  const error = await screen.findByTestId("proposal-error");
  expect(error.textContent).toContain("反映しませんでした");
  expect(error.textContent).toContain("最新の一覧を表示しています");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(screen.queryByTestId("proposal-notice")).toBeNull();
  await waitFor(() => expect(cells(currentRow("R04"))[0]).toBe("2")); // 最新のR04（版2）
});

test.each([
  ["対象がない", { target: undefined }],
  ["追加する条件がない", { added_conditions: undefined }],
  ["追加する条件が空", { added_conditions: {} }],
])("更新案の応答が不完全（%s）: 空のIDや空の差分のプレビューは出さず、理由を出し、現行の一覧は変わらない", async (_name, broken) => {
  stubFetch({ ...baseRoutes(), [PROPOSALS]: { status: 201, body: { ...updateBody, ...broken } } });
  render(<RuleAdmin />);
  await requestUpdate();
  const error = await screen.findByTestId("proposal-error");
  expect(error.textContent).toContain("対象のルールまたは追加する条件を読み取れませんでした");
  expect(error.textContent).toContain("判定ルールの一覧は変わっていません");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(screen.queryByTestId("proposal-notice")).toBeNull();
  expect(screen.queryByRole("button", { name: "Yes" })).toBeNull(); // 反映の操作を出さない
  expect(rowsOf(current())).toBe(4);
  expect(cells(currentRow("R04"))[0]).toBe("1");
});

test("定型の更新案: Noでは再提案せず、「AIで再提案」が元の依頼でforce_aiを送り、生成元が切り替わる", async () => {
  const sent: unknown[] = [];
  stubFetch({
    ...baseRoutes(),
    [PROPOSALS]: (init) => {
      const body = JSON.parse(init?.body as string);
      sent.push(body);
      return { status: 201, body: { ...updateBody, source: { kind: body.force_ai ? "bedrock" : "template" } } };
    },
    [DECISION]: { body: { applied: false, ...listBody() } },
  });
  render(<RuleAdmin />);
  await requestUpdate();
  await screen.findByTestId("proposal");
  expect(screen.getByTestId("source-badge").getAttribute("data-source")).toBe("template");
  fireEvent.click(screen.getByRole("button", { name: "No", exact: true }));
  const retry = await screen.findByRole("button", { name: "AIで再提案" });
  expect(sent).toEqual([{ prompt: PROMPT }]); // Noだけでは、再提案の要求を送らない
  expect(cells(currentRow("R04"))[0]).toBe("1"); // 承認済みの一覧は元のまま
  fireEvent.click(retry);
  const proposal = await screen.findByTestId("proposal");
  expect(sent).toEqual([{ prompt: PROMPT }, { prompt: PROMPT, force_ai: true }]);
  expect(screen.getByTestId("source-badge").getAttribute("data-source")).toBe("bedrock");
  expect(within(proposal).getByTestId("update-summary").textContent).toContain("更新の対象: ルールR04"); // 再提案も更新案
  expect(screen.queryByRole("button", { name: "AIで再提案" })).toBeNull();
});

test("追加案・削除案の見出しと通知は、これまでどおり（更新案の文言に変わらない）", async () => {
  const added = rule("R05", "caution", { A09: "yes" });
  stubFetch({
    ...baseRoutes(),
    [PROPOSALS]: { status: 201, body: { proposal_id: "a1", kind: "add", before: baseRules, after: [...baseRules, added], added, source: { kind: "mock" } } },
  });
  render(<RuleAdmin />);
  await requestUpdate("設問A09がYesのとき注意とするルールを追加して");
  const proposal = await screen.findByTestId("proposal");
  expect(within(proposal).getByRole("heading", { name: /判定ルールの追加案の確認/ })).toBeTruthy();
  expect(within(proposal).queryByTestId("update-summary")).toBeNull();
  expect(proposal.querySelector(".row-updated")).toBeNull();
});
