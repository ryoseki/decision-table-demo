// 実行: npm test（Vitest + jsdom）。一覧の［サンプル案件を作成］を、fetch のスタブで確認する。
// サーバーには接続しない。POST /api/sample-cases の応答は、テストが決める（保留・成功・作成済み・失敗）。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";
import CaseList, { type ListFilter } from "./CaseList";
import { apiRow } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function Harness() {
  const [filter, setFilter] = useState<ListFilter>({ query: "", status: "all" });
  return <CaseList filter={filter} onFilterChange={setFilter} />;
}

const SAMPLE_IDS = ["CASE-SAMPLE-01", "CASE-SAMPLE-02", "CASE-SAMPLE-03", "CASE-SAMPLE-04"];
const sampleRows = () => SAMPLE_IDS.map((id) => apiRow({ case_id: id }));
const created = (ids: string[] = SAMPLE_IDS) => ({
  status: 201,
  body: { created: ids, existing: SAMPLE_IDS.filter((i) => !ids.includes(i)), case_ids: SAMPLE_IDS },
});
const alreadyThere = { status: 200, body: { created: [], existing: SAMPLE_IDS, case_ids: SAMPLE_IDS } };

type Reply = { status?: number; body?: unknown } | Error;

/**
 * GET /api/cases と POST /api/sample-cases を、呼ばれた順の応答で返す fetch にする。
 * POST の応答は、関数で渡すと、解決のタイミングをテストが決められる（保留）。
 */
function stubApi(opts: { lists: Reply[]; posts: (Reply | (() => Promise<Reply>))[] }) {
  const calls: string[] = [];
  const lists = [...opts.lists];
  const posts = [...opts.posts];
  const spy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    calls.push(key);
    let next: Reply | (() => Promise<Reply>) | undefined;
    if (key === "GET /api/cases") next = lists.shift() ?? opts.lists[opts.lists.length - 1];
    else if (key === "POST /api/sample-cases") next = posts.shift();
    else throw new Error(`想定外のリクエスト: ${key}`);
    if (next === undefined) throw new Error(`応答の用意がない: ${key}`);
    const reply = typeof next === "function" ? await next() : next;
    if (reply instanceof Error) throw reply;
    const status = reply.status ?? 200;
    return new Response(JSON.stringify(reply.body ?? {}), { status });
  });
  vi.stubGlobal("fetch", spy);
  return { spy, calls, count: (key: string) => calls.filter((c) => c === key).length };
}

const button = () => screen.getByRole("button", { name: /サンプル案件を作成|作成中/ });
const idsShown = () =>
  screen.queryAllByRole("row").slice(1).map((r) => within(r).getByRole("rowheader").textContent);

test("空の一覧でも、一覧のどの状態でも、［サンプル案件を作成］が操作できる", async () => {
  stubApi({ lists: [{ body: [] }], posts: [] });
  render(<Harness />);
  expect(button().hasAttribute("disabled")).toBe(false); // 読み込み中でも見える
  await screen.findByTestId("empty-list");
  expect(button().hasAttribute("disabled")).toBe(false);
  expect(screen.getByText(/作成済みの場合は、増えません/)).toBeTruthy();
});

test("一覧の取得に失敗していても、ボタンは使える（一覧のエラーとは別の表示）", async () => {
  stubApi({ lists: [{ status: 503 }, { body: sampleRows() }], posts: [created()] });
  render(<Harness />);
  await screen.findByTestId("list-error");
  fireEvent.click(button());
  await screen.findByTestId("sample-notice");
  await screen.findByRole("table");
  expect(idsShown()).toEqual(SAMPLE_IDS);
});

test("押すとPOSTを1回送り、成功したら一覧を取り直して、再読み込みなしで4件を表示する", async () => {
  const api = stubApi({ lists: [{ body: [] }, { body: sampleRows() }], posts: [created()] });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  expect(api.count("GET /api/cases")).toBe(1);
  fireEvent.click(button());
  await screen.findByRole("table");
  expect(api.count("POST /api/sample-cases")).toBe(1);
  expect(api.count("GET /api/cases")).toBe(2); // 取り直した
  expect(idsShown()).toEqual(SAMPLE_IDS);
  expect(screen.getByTestId("sample-notice").textContent).toContain("4件作成しました");
  expect(screen.queryByTestId("sample-error")).toBeNull();
  expect(screen.queryByTestId("empty-list")).toBeNull();
});

test("作成済みの場合は、新しく作っていないと知らせ、一覧を取り直す（件数は増えない）", async () => {
  const api = stubApi({ lists: [{ body: sampleRows() }, { body: sampleRows() }], posts: [alreadyThere] });
  render(<Harness />);
  await screen.findByRole("table");
  fireEvent.click(button());
  await waitFor(() => expect(api.count("GET /api/cases")).toBe(2));
  await screen.findByRole("table");
  expect(screen.getByTestId("sample-notice").textContent).toContain("作成済み");
  expect(screen.getByTestId("sample-notice").textContent).not.toContain("作成しました");
  expect(idsShown()).toEqual(SAMPLE_IDS);
  expect(screen.getByText(/全4件）$/).textContent).toContain("4件を表示");
});

test("作成中はボタンを無効にして「作成中…」を出し、連打してもPOSTは1回だけ", async () => {
  let release: (r: Reply) => void = () => {};
  const pending = new Promise<Reply>((resolve) => (release = resolve));
  const api = stubApi({ lists: [{ body: [] }, { body: sampleRows() }], posts: [() => pending] });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  const target = button();
  fireEvent.click(target);
  fireEvent.click(target);
  fireEvent.click(target); // 3回目（fireEventは各クリックのあとに描画を挟む。ここで確かめるのは、2回目以降が送られないこと）
  await waitFor(() => expect(button().textContent).toBe("作成中…"));
  expect(button().hasAttribute("disabled")).toBe(true);
  expect(button().getAttribute("aria-busy")).toBe("true");
  fireEvent.click(button()); // 無効なボタンの操作
  expect(api.count("POST /api/sample-cases")).toBe(1);
  release(created());
  await screen.findByRole("table");
  expect(button().textContent).toBe("サンプル案件を作成");
  expect(button().hasAttribute("disabled")).toBe(false);
  expect(api.count("POST /api/sample-cases")).toBe(1);
});

test("失敗したら、理由と再試行できることを表示し、一覧は取り直さず、押し直せる。成功すると表示が消える", async () => {
  const api = stubApi({
    lists: [{ body: [] }, { body: sampleRows() }],
    posts: [{ status: 503 }, created()],
  });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.click(button());
  const alert = await screen.findByTestId("sample-error");
  expect(alert.getAttribute("role")).toBe("alert");
  expect(alert.textContent).toContain("作成できませんでした");
  expect(alert.textContent).toContain("HTTP 503"); // 理由
  expect(alert.textContent).toContain("混み合っています");
  expect(alert.textContent).toContain("再試行できます"); // 再試行の案内
  expect(button().hasAttribute("disabled")).toBe(false);
  expect(api.count("GET /api/cases")).toBe(1); // 失敗では取り直さない
  expect(screen.queryByTestId("sample-notice")).toBeNull();

  fireEvent.click(button()); // 再試行
  await screen.findByRole("table");
  expect(api.count("POST /api/sample-cases")).toBe(2);
  expect(screen.queryByTestId("sample-error")).toBeNull();
  expect(idsShown()).toEqual(SAMPLE_IDS);
});

test("接続できない・拒否された（405）場合も、理由を出して再試行できる", async () => {
  stubApi({
    lists: [{ body: [] }],
    posts: [new TypeError("network"), { status: 405 }],
  });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.click(button());
  expect((await screen.findByTestId("sample-error")).textContent).toContain("接続できませんでした");
  fireEvent.click(button());
  await waitFor(() => expect(screen.getByTestId("sample-error").textContent).toContain("HTTP 405"));
  expect(button().hasAttribute("disabled")).toBe(false);
});

test("作成後に、検索語と状態の絞り込みを保ったまま一覧が更新される", async () => {
  stubApi({ lists: [{ body: [] }, { body: sampleRows() }], posts: [created()] });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "SAMPLE-03" } });
  fireEvent.click(button());
  await screen.findByRole("table");
  expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("SAMPLE-03");
  expect(idsShown()).toEqual(["CASE-SAMPLE-03"]);
});

test("作成に成功したあと一覧の取得に失敗したら、成功の通知と一覧のエラーが両方出る（作成は取り消さない）", async () => {
  const api = stubApi({ lists: [{ body: [] }, { status: 503 }], posts: [created()] });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.click(button());
  await screen.findByTestId("list-error");
  expect(screen.getByTestId("sample-notice").textContent).toContain("4件作成しました");
  expect(screen.queryByTestId("sample-error")).toBeNull(); // 作成の失敗ではない
  expect(api.count("POST /api/sample-cases")).toBe(1);
  expect(api.count("GET /api/cases")).toBe(2);
  expect(button().hasAttribute("disabled")).toBe(false);
});

test("成功の通知が出ている状態で次の作成が失敗したら、成功の通知は消え、理由と再試行の案内が出る", async () => {
  const api = stubApi({
    lists: [{ body: [] }, { body: sampleRows() }],
    posts: [created(), { status: 503 }],
  });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.click(button());
  await screen.findByRole("table");
  expect(screen.getByTestId("sample-notice").textContent).toContain("4件作成しました");

  fireEvent.click(button());
  const alert = await screen.findByTestId("sample-error");
  expect(screen.queryByTestId("sample-notice")).toBeNull(); // 古い成功の通知を残さない
  expect(alert.textContent).toContain("HTTP 503");
  expect(alert.textContent).toContain("再試行できます");
  expect(api.count("POST /api/sample-cases")).toBe(2);
  expect(api.count("GET /api/cases")).toBe(2); // 失敗では取り直さない
  expect(idsShown()).toEqual(SAMPLE_IDS); // すでにある一覧はそのまま
});

test("一部だけ作成済みの状態で、503のあと再試行すると、残りの2件だけ作成と通知し、全4件を取り直して表示する（古いエラーは消える）", async () => {
  const api = stubApi({
    lists: [{ body: [] }, { body: sampleRows() }],
    posts: [{ status: 503 }, created(SAMPLE_IDS.slice(2))], // 先の2件は作成済み、今回は後ろの2件だけ作成
  });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.click(button());
  await screen.findByTestId("sample-error");
  expect(api.count("GET /api/cases")).toBe(1); // 失敗では取り直さない

  fireEvent.click(button()); // 再試行
  const notice = await screen.findByTestId("sample-notice");
  await screen.findByRole("table");
  expect(notice.getAttribute("aria-live")).toBe("polite");
  expect(notice.textContent).toContain("2件作成しました");
  expect(notice.textContent).toContain("全4件");
  expect(screen.queryByTestId("sample-error")).toBeNull(); // 古いエラーは消える
  expect(api.count("POST /api/sample-cases")).toBe(2);
  expect(api.count("GET /api/cases")).toBe(2); // 取り直した
  expect(idsShown()).toEqual(SAMPLE_IDS);
});
