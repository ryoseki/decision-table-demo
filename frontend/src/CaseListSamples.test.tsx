// 実行: npm test（Vitest + jsdom）。一覧の［サンプル案件を作成］を、fetch のスタブで確認する。
// サーバーには接続しない。POST /api/sample-cases の応答は、テストが決める（保留・成功・作成済み・失敗）。
// 1回の操作（依頼ID）につき案件1件。成功したら次の押下は新しい操作、失敗したら同じ依頼IDで再試行する。
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";
import CaseList, { type ListFilter } from "./CaseList";
import { apiRow } from "./testing";
import { useSampleCreation } from "./useApi";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// App と同じ形: フィルターと作成の状態は親が持ち、一覧は別の画面へ切り替えるとアンマウントされる。
function Harness() {
  const [filter, setFilter] = useState<ListFilter>({ query: "", status: "all" });
  const [shown, setShown] = useState(true);
  const sample = useSampleCreation();
  return (
    <>
      <button type="button" onClick={() => setShown((s) => !s)}>画面の切り替え</button>
      {shown ? <CaseList filter={filter} onFilterChange={setFilter} sample={sample} /> : <p data-testid="elsewhere">別の画面</p>}
    </>
  );
}
const toggle = () => fireEvent.click(screen.getByRole("button", { name: "画面の切り替え" }));
const leave = () => {
  toggle();
  expect(screen.queryByTestId("elsewhere")).not.toBeNull();
};
const back = () => {
  toggle();
  expect(screen.queryByTestId("elsewhere")).toBeNull();
};

const idOf = (n: number) => `CASE-SAMPLE-${String(n).padStart(16, "0")}`;
const rowsOf = (count: number) => Array.from({ length: count }, (_, i) => apiRow({ case_id: idOf(i + 1) }));
const created = (n: number) => ({ status: 201, body: { case_id: idOf(n), created: true } });
const alreadyThere = (n: number) => ({ status: 200, body: { case_id: idOf(n), created: false } });

type Reply = { status?: number; body?: unknown; raw?: string } | Error;

/**
 * GET /api/cases と POST /api/sample-cases を、呼ばれた順の応答で返す fetch にする。
 * POST の応答は、関数で渡すと、解決のタイミングをテストが決められる（保留）。
 * keys は、POST ごとの Idempotency-Key（依頼ID）。
 */
function stubApi(opts: { lists: Reply[]; posts: (Reply | (() => Promise<Reply>))[] }) {
  const calls: string[] = [];
  const keys: (string | null)[] = [];
  const lists = [...opts.lists];
  const posts = [...opts.posts];
  const spy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    calls.push(key);
    let next: Reply | (() => Promise<Reply>) | undefined;
    if (key === "GET /api/cases") next = lists.shift() ?? opts.lists[opts.lists.length - 1];
    else if (key === "POST /api/sample-cases") {
      keys.push(new Headers(init?.headers).get("Idempotency-Key"));
      next = posts.shift();
    } else throw new Error(`想定外のリクエスト: ${key}`);
    if (next === undefined) throw new Error(`応答の用意がない: ${key}`);
    const reply = typeof next === "function" ? await next() : next;
    if (reply instanceof Error) throw reply;
    const status = reply.status ?? 200;
    return new Response(reply.raw ?? JSON.stringify(reply.body ?? {}), { status });
  });
  vi.stubGlobal("fetch", spy);
  return { spy, calls, keys, count: (key: string) => calls.filter((c) => c === key).length };
}

const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/;
const button = () => screen.getByRole("button", { name: /サンプル案件を作成|作成中/ });
const idsShown = () =>
  screen.queryAllByRole("row").slice(1).map((r) => within(r).getByRole("rowheader").textContent);

test("空の一覧でも、一覧のどの状態でも、［サンプル案件を作成］が操作できる。1件ずつ足すと案内する", async () => {
  stubApi({ lists: [{ body: [] }], posts: [] });
  render(<Harness />);
  expect(button().hasAttribute("disabled")).toBe(false); // 読み込み中でも見える
  await screen.findByTestId("empty-list");
  expect(button().hasAttribute("disabled")).toBe(false);
  expect(screen.getByText(/押すたびに、架空の案件を1件追加します/)).toBeTruthy();
  // 固定の件数や「増えない」という古い案内は出さない
  expect(document.body.textContent).not.toMatch(/全4件|架空の4件|増えません/);
});

test("一覧の取得に失敗していても、ボタンは使える（一覧のエラーとは別の表示）", async () => {
  stubApi({ lists: [{ status: 503 }, { body: rowsOf(1) }], posts: [created(1)] });
  render(<Harness />);
  await screen.findByTestId("list-error");
  fireEvent.click(button());
  await screen.findByTestId("sample-notice");
  await screen.findByRole("table");
  expect(idsShown()).toEqual([idOf(1)]);
});

test("押すと依頼ID付きのPOSTを1回送り、成功したら一覧を取り直して、再読み込みなしで1件を表示する", async () => {
  const api = stubApi({ lists: [{ body: [] }, { body: rowsOf(1) }], posts: [created(1)] });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  expect(api.count("GET /api/cases")).toBe(1);
  fireEvent.click(button());
  await screen.findByRole("table");
  expect(api.count("POST /api/sample-cases")).toBe(1);
  expect(api.keys[0]).toMatch(UUID);
  expect(api.count("GET /api/cases")).toBe(2); // 取り直した
  expect(idsShown()).toEqual([idOf(1)]);
  const notice = screen.getByTestId("sample-notice");
  expect(notice.textContent).toContain("1件作成しました");
  expect(notice.textContent).toContain(idOf(1));
  expect(screen.queryByTestId("sample-error")).toBeNull();
  expect(screen.queryByTestId("empty-list")).toBeNull();
});

test("成功したあとの押下は新しい操作（別の依頼ID）で、押すたびに1件ずつ増える。4件を超えても止まらない", async () => {
  const lists = Array.from({ length: 7 }, (_, i) => ({ body: rowsOf(i) }));
  const api = stubApi({ lists, posts: [1, 2, 3, 4, 5, 6].map(created) });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  for (let n = 1; n <= 6; n++) {
    fireEvent.click(button());
    await waitFor(() => expect(api.count("GET /api/cases")).toBe(n + 1));
    await waitFor(() => expect(button().hasAttribute("disabled")).toBe(false));
    await waitFor(() => expect(idsShown()).toHaveLength(n));
    expect(screen.getByTestId("sample-notice").textContent).toContain(idOf(n));
  }
  expect(idsShown()).toEqual([1, 2, 3, 4, 5, 6].map(idOf)); // 6件（4件の上限はない）
  expect(api.keys).toHaveLength(6);
  expect(new Set(api.keys).size).toBe(6); // 毎回、別の依頼ID
  for (const key of api.keys) expect(key).toMatch(UUID);
});

test("再送で作成済みと返ったら、新しく作っていないと知らせ、一覧を取り直す。その次の押下は新しい操作", async () => {
  const api = stubApi({
    lists: [{ body: rowsOf(1) }, { body: rowsOf(1) }, { body: rowsOf(2) }],
    posts: [alreadyThere(1), created(2)],
  });
  render(<Harness />);
  await screen.findByRole("table");
  fireEvent.click(button());
  await waitFor(() => expect(api.count("GET /api/cases")).toBe(2));
  const notice = await screen.findByTestId("sample-notice");
  expect(notice.textContent).toContain("作成済み");
  expect(notice.textContent).toContain("新しく作成した案件はありません");
  expect(notice.textContent).not.toContain("作成しました");
  expect(idsShown()).toEqual([idOf(1)]);
  await waitFor(() => expect(button().hasAttribute("disabled")).toBe(false));

  fireEvent.click(button());
  await waitFor(() => expect(idsShown()).toEqual([idOf(1), idOf(2)]));
  expect(api.keys[1]).not.toBe(api.keys[0]); // 成功（再送済みを含む）のあとは、新しい操作
});

test("作成中はボタンを無効にして「作成中…」を出し、連打してもPOSTは1回だけ", async () => {
  let release: (r: Reply) => void = () => {};
  const pending = new Promise<Reply>((resolve) => (release = resolve));
  const api = stubApi({ lists: [{ body: [] }, { body: rowsOf(1) }], posts: [() => pending] });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  const target = button();
  fireEvent.click(target);
  fireEvent.click(target);
  fireEvent.click(target); // 連打。クリックのたびに再描画され、2回目以降は無効なボタンになる
  await waitFor(() => expect(button().textContent).toBe("作成中…"));
  expect(button().hasAttribute("disabled")).toBe(true);
  expect(button().getAttribute("aria-busy")).toBe("true");
  fireEvent.click(button()); // 無効なボタンの操作
  expect(api.count("POST /api/sample-cases")).toBe(1);
  release(created(1));
  await screen.findByRole("table");
  expect(button().textContent).toBe("サンプル案件を作成");
  expect(button().hasAttribute("disabled")).toBe(false);
  expect(api.count("POST /api/sample-cases")).toBe(1);
});

test("失敗したら、理由と再試行できることを表示し、一覧は取り直さない。再試行は同じ依頼IDで、成功すると表示が消える", async () => {
  const api = stubApi({
    lists: [{ body: [] }, { body: rowsOf(1) }],
    posts: [{ status: 503 }, created(1)],
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
  expect(alert.textContent).toContain("案件は重複しません"); // 1件の再試行であること
  expect(alert.textContent).not.toMatch(/全4件|4件/);
  expect(button().hasAttribute("disabled")).toBe(false);
  expect(api.count("GET /api/cases")).toBe(1); // 失敗では取り直さない
  expect(screen.queryByTestId("sample-notice")).toBeNull();

  fireEvent.click(button()); // 再試行
  await screen.findByRole("table");
  expect(api.count("POST /api/sample-cases")).toBe(2);
  expect(api.keys[1]).toBe(api.keys[0]); // 同じ操作の再試行は、同じ依頼ID
  expect(screen.queryByTestId("sample-error")).toBeNull();
  expect(idsShown()).toEqual([idOf(1)]);
});

test("接続できない・拒否（405）・応答を読めない場合も、理由を出し、同じ依頼IDで再試行できる。成功後は新しい依頼ID", async () => {
  const api = stubApi({
    lists: [{ body: [] }, { body: rowsOf(1) }, { body: rowsOf(2) }],
    posts: [new TypeError("network"), { status: 405 }, { status: 201, raw: "<html>not json</html>" }, created(1), created(2)],
  });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.click(button());
  expect((await screen.findByTestId("sample-error")).textContent).toContain("接続できませんでした");
  fireEvent.click(button());
  await waitFor(() => expect(screen.getByTestId("sample-error").textContent).toContain("HTTP 405"));
  fireEvent.click(button());
  await waitFor(() => expect(screen.getByTestId("sample-error").textContent).toContain("応答を読み取れませんでした"));
  expect(button().hasAttribute("disabled")).toBe(false);
  expect(screen.getByTestId("sample-error").textContent).toContain("再試行できます");
  fireEvent.click(button()); // 4回目: 成功
  await screen.findByRole("table");
  expect(new Set(api.keys.slice(0, 4)).size).toBe(1); // 失敗のあいだは、同じ依頼ID
  fireEvent.click(button()); // 成功のあと: 新しい操作
  await waitFor(() => expect(api.keys).toHaveLength(5));
  expect(api.keys[4]).not.toBe(api.keys[0]);
});

test("作成後に、検索語と状態の絞り込みを保ったまま一覧が更新される", async () => {
  stubApi({ lists: [{ body: [] }, { body: rowsOf(3) }], posts: [created(3)] });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: idOf(3) } });
  fireEvent.click(button());
  await screen.findByRole("table");
  expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe(idOf(3));
  expect(idsShown()).toEqual([idOf(3)]);
});

test("作成に成功したあと一覧の取得に失敗したら、成功の通知と一覧のエラーが両方出る（作成は取り消さない）", async () => {
  const api = stubApi({ lists: [{ body: [] }, { status: 503 }], posts: [created(1)] });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.click(button());
  await screen.findByTestId("list-error");
  expect(screen.getByTestId("sample-notice").textContent).toContain("1件作成しました");
  expect(screen.queryByTestId("sample-error")).toBeNull(); // 作成の失敗ではない
  expect(api.count("POST /api/sample-cases")).toBe(1);
  expect(api.count("GET /api/cases")).toBe(2);
  expect(button().hasAttribute("disabled")).toBe(false);
});

test("成功の通知が出ている状態で次の作成が失敗したら、成功の通知は消え、理由と再試行の案内が出る", async () => {
  const api = stubApi({
    lists: [{ body: [] }, { body: rowsOf(1) }],
    posts: [created(1), { status: 503 }],
  });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.click(button());
  await screen.findByRole("table");
  expect(screen.getByTestId("sample-notice").textContent).toContain("1件作成しました");

  fireEvent.click(button());
  const alert = await screen.findByTestId("sample-error");
  expect(screen.queryByTestId("sample-notice")).toBeNull(); // 古い成功の通知を残さない
  expect(alert.textContent).toContain("HTTP 503");
  expect(alert.textContent).toContain("再試行できます");
  expect(api.count("POST /api/sample-cases")).toBe(2);
  expect(api.keys[1]).not.toBe(api.keys[0]); // 成功のあとの押下は新しい操作
  expect(api.count("GET /api/cases")).toBe(2); // 失敗では取り直さない
  expect(idsShown()).toEqual([idOf(1)]); // すでにある一覧はそのまま
});

test("失敗のあとの再試行で、すでに作成済みだった（結果が分からなかっただけ）と分かっても、重複せず、一覧を取り直す", async () => {
  const api = stubApi({
    lists: [{ body: [] }, { body: rowsOf(1) }],
    posts: [new TypeError("network"), alreadyThere(1)], // 1回目は、実際には作成されていた
  });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.click(button());
  await screen.findByTestId("sample-error");
  fireEvent.click(button());
  const notice = await screen.findByTestId("sample-notice");
  expect(notice.textContent).toContain("作成済み");
  await screen.findByRole("table");
  expect(idsShown()).toEqual([idOf(1)]); // 1件のまま
  expect(screen.queryByTestId("sample-error")).toBeNull();
  expect(api.keys[1]).toBe(api.keys[0]);
  expect(api.count("GET /api/cases")).toBe(2);
});

// ---- 画面の切り替え（一覧のアンマウント → 再マウント）。作成の状態は親（App）が持つ ----

test("通信失敗のあと、別の画面へ移って一覧へ戻っても、再試行は同じ依頼ID。成功すると、次の押下は新しい依頼ID", async () => {
  const api = stubApi({
    lists: [{ body: [] }, { body: [] }, { body: rowsOf(1) }, { body: rowsOf(2) }],
    posts: [new TypeError("network"), created(1), created(2)], // 1回目は、実際には作成されていたかもしれない
  });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.click(button());
  await screen.findByTestId("sample-error");

  leave();
  expect(screen.queryByTestId("sample-error")).toBeNull();
  back();
  await screen.findByTestId("empty-list");
  expect(screen.getByTestId("sample-error").textContent).toContain("再試行できます"); // 失敗の表示も保たれる
  fireEvent.click(button());
  await screen.findByRole("table");
  expect(api.keys).toHaveLength(2);
  expect(api.keys[1]).toBe(api.keys[0]); // 画面を移っても、同じ操作の再試行
  expect(screen.queryByTestId("sample-error")).toBeNull();

  fireEvent.click(button()); // 成功のあと: 新しい操作
  await waitFor(() => expect(api.keys).toHaveLength(3));
  expect(api.keys[2]).not.toBe(api.keys[0]);
  await waitFor(() => expect(idsShown()).toEqual([idOf(1), idOf(2)]));
});

test("作成中に別の画面へ移って戻っても、ボタンは作成中のまま。POSTは1回だけで、成功は一覧に反映される", async () => {
  let release: (r: Reply) => void = () => {};
  const pending = new Promise<Reply>((resolve) => (release = resolve));
  const api = stubApi({
    lists: [{ body: [] }, { body: [] }, { body: rowsOf(1) }, { body: rowsOf(2) }],
    posts: [() => pending, created(2)],
  });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.click(button());
  await waitFor(() => expect(button().textContent).toBe("作成中…"));

  leave();
  back();
  await screen.findByTestId("empty-list");
  expect(button().textContent).toBe("作成中…"); // 再マウントしても、作成中
  expect(button().hasAttribute("disabled")).toBe(true);
  fireEvent.click(button());
  expect(api.count("POST /api/sample-cases")).toBe(1); // 二重に作らない

  release(created(1));
  await screen.findByRole("table");
  expect(screen.getByTestId("sample-notice").textContent).toContain(idOf(1));
  expect(button().hasAttribute("disabled")).toBe(false);

  fireEvent.click(button());
  await waitFor(() => expect(api.keys).toHaveLength(2));
  expect(api.keys[1]).not.toBe(api.keys[0]); // 成功が遅れて届いたあとも、次は新しい操作
});

test("作成中に一覧を離れ、離れている間に失敗が届いたら、戻ったときに理由が出て、同じ依頼IDで再試行できる", async () => {
  let release: (r: Reply) => void = () => {};
  const pending = new Promise<Reply>((resolve) => (release = resolve));
  const api = stubApi({
    lists: [{ body: [] }, { body: [] }, { body: rowsOf(1) }],
    posts: [() => pending, created(1)],
  });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.click(button());
  await waitFor(() => expect(button().textContent).toBe("作成中…"));
  leave();
  await act(async () => release({ status: 503 })); // 離れている間に失敗が届く
  back();
  await screen.findByTestId("sample-error");
  expect(button().hasAttribute("disabled")).toBe(false);
  fireEvent.click(button());
  await screen.findByRole("table");
  expect(api.keys[1]).toBe(api.keys[0]);
});
