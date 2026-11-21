// 実行: npm test（Vitest + jsdom）。一覧の［サンプル案件を作成］を、fetch のスタブで確認する。
// サーバーには接続しない。POST /api/sample-cases の応答は、テストが決める。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";
import CaseList, { type ListFilter } from "./CaseList";
import { apiRow } from "./testing";
import { useSampleCreation } from "./useApi";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// App と同じ形: フィルターと作成の状態は親が持つ。
function Harness() {
  const [filter, setFilter] = useState<ListFilter>({ query: "", status: "all" });
  const sample = useSampleCreation();
  return <CaseList filter={filter} onFilterChange={setFilter} sample={sample} />;
}

// 実際と同じ13文字の案件IDを表示する。
const idOf = (n: number) => `CASE-${n.toString(16).toUpperCase().padStart(8, "0")}`;
const rowsOf = (count: number) => Array.from({ length: count }, (_, i) => apiRow({ case_id: idOf(i + 1) }));
const created = (n: number) => ({ status: 201, body: { case_id: idOf(n), created: true } });

type Reply = { status?: number; body?: unknown };

/** GET /api/cases と POST /api/sample-cases を、呼ばれた順の応答で返す fetch にする。POST は関数で渡すと保留にできる。 */
function stubApi(opts: { lists: Reply[]; posts: (Reply | (() => Promise<Reply>))[] }) {
  const keys: (string | null)[] = [];
  const lists = [...opts.lists];
  const posts = [...opts.posts];
  let postCount = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      let next: Reply | (() => Promise<Reply>) | undefined;
      if (key === "GET /api/cases") next = lists.shift() ?? opts.lists[opts.lists.length - 1];
      else if (key === "POST /api/sample-cases") {
        postCount += 1;
        keys.push(new Headers(init?.headers).get("Idempotency-Key"));
        next = posts.shift();
      } else throw new Error(`想定外のリクエスト: ${key}`);
      if (next === undefined) throw new Error(`応答の用意がない: ${key}`);
      const reply = typeof next === "function" ? await next() : next;
      return new Response(JSON.stringify(reply.body ?? {}), { status: reply.status ?? 200 });
    }),
  );
  return { keys, postCount: () => postCount };
}

const button = () => screen.getByRole("button", { name: /サンプル案件を作成|作成中/ });
const idsShown = () =>
  screen.queryAllByRole("row").slice(1).map((r) => within(r).getByRole("rowheader").textContent);

test("押すと一覧に1件追加され、次に押してもさらに1件増える。13文字の案件IDの詳細へ進める", async () => {
  const api = stubApi({
    lists: [{ body: [] }, { body: rowsOf(1) }, { body: rowsOf(2) }],
    posts: [created(1), created(2)],
  });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.click(button());
  await screen.findByRole("table");
  expect(idsShown()).toEqual([idOf(1)]);
  await waitFor(() => expect(button().hasAttribute("disabled")).toBe(false));

  fireEvent.click(button());
  await waitFor(() => expect(idsShown()).toEqual([idOf(1), idOf(2)]));
  expect(api.keys[1]).not.toBe(api.keys[0]); // 成功のあとの押下は、新しい操作
  expect(screen.getByTestId("sample-notice").textContent).toContain(idOf(2));
  expect(idOf(2)).toHaveLength(13);
  expect(screen.getByRole("link", { name: "審査を開く（" + idOf(2) + "）" }).getAttribute("href")).toBe(`#/cases/${idOf(2)}`);
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
  await waitFor(() => expect(button().textContent).toBe("作成中…"));
  expect(button().hasAttribute("disabled")).toBe(true);
  expect(api.postCount()).toBe(1);
  release(created(1));
  await screen.findByRole("table");
  expect(button().textContent).toBe("サンプル案件を作成");
  expect(button().hasAttribute("disabled")).toBe(false);
});

test("失敗したら、作成できなかったことと再試行できることを表示する", async () => {
  stubApi({ lists: [{ body: [] }], posts: [{ status: 503 }] });
  render(<Harness />);
  await screen.findByTestId("empty-list");
  fireEvent.click(button());
  const alert = await screen.findByTestId("sample-error");
  expect(alert.getAttribute("role")).toBe("alert");
  expect(alert.textContent).toContain("作成できませんでした");
  expect(alert.textContent).toContain("再試行できます");
  expect(button().hasAttribute("disabled")).toBe(false);
});
