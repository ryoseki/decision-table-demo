// 実行: npm test（Vitest + jsdom）。審査対象の一覧を、fetch のスタブで確認する（サーバーに接続しない）。
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";
import CaseList, { type ListFilter } from "./CaseList";
import { apiRow, stubFetch } from "./testing";
import { useSampleCreation } from "./useApi";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// 検索語と状態は App が持つため、同じ形で包む。
function Harness() {
  const [filter, setFilter] = useState<ListFilter>({ query: "", status: "all" });
  const sample = useSampleCreation();
  return <CaseList filter={filter} onFilterChange={setFilter} sample={sample} />;
}

const ROWS = [
  apiRow({ case_id: "CASE-A", user_id: "123", summary: "ログインの通知", inquiry: { subject: "s", body: "知らない端末" } }),
  apiRow({
    case_id: "CASE-B",
    user_id: "456",
    summary: "APIキーの通知",
    inquiry: { subject: "s", body: "自分では発行していない" },
    counts: { yes: 0, no: 0, pending: 1, unanswered: 11 },
  }),
  apiRow({ case_id: "CASE-C", user_id: "789", summary: "メールの変更", result_code: "no_issue" }),
];

const rowsShown = () => screen.queryAllByRole("row").slice(1).map((r) => within(r).getByRole("rowheader").textContent);

test("取得中は読み込み中を表示し、取得できたら、行を案件ID順のまま表示する", async () => {
  stubFetch({ "GET /api/cases": { body: ROWS } });
  render(<Harness />);
  expect(screen.getByTestId("list-loading")).toBeTruthy();
  await screen.findByRole("table");
  expect(screen.queryByTestId("list-loading")).toBeNull();
  expect(rowsShown()).toEqual(["CASE-A", "CASE-B", "CASE-C"]);
  expect(screen.getByText(/Showing/).textContent).toContain("Showing 3 of 3");
});

test("各行に、user_id、概要、回答の件数、状態（結果と確認待ちから導く）を表示する", async () => {
  stubFetch({ "GET /api/cases": { body: ROWS } });
  render(<Harness />);
  await screen.findByRole("table");
  const row = (id: string) => screen.getByText(id).closest("tr")!;
  expect(row("CASE-A").textContent).toContain("user_id 123");
  expect(row("CASE-A").textContent).toContain("ログインの通知");
  expect(within(row("CASE-A")).getByText("Not reviewed")).toBeTruthy();
  expect(within(row("CASE-B")).getByText("Pending data")).toBeTruthy(); // 確認待ちがある
  expect(within(row("CASE-B")).getByText("Pending 1")).toBeTruthy();
  expect(within(row("CASE-C")).getByText("Registered")).toBeTruthy(); // 結果がある
});

test("各行から、案件の詳細へ移れる（リンク先は #/cases/<案件ID>）", async () => {
  stubFetch({ "GET /api/cases": { body: ROWS } });
  render(<Harness />);
  await screen.findByRole("table");
  const link = screen.getByRole("link", { name: /Open review.*CASE-B/ });
  expect(link.getAttribute("href")).toBe("#/cases/CASE-B");
});

test("検索: 案件ID・user_id・概要・申告内容（本文）で絞り込む", async () => {
  stubFetch({ "GET /api/cases": { body: ROWS } });
  render(<Harness />);
  await screen.findByRole("table");
  const search = screen.getByRole("searchbox");
  fireEvent.change(search, { target: { value: "case-b" } });
  expect(rowsShown()).toEqual(["CASE-B"]);
  fireEvent.change(search, { target: { value: "789" } });
  expect(rowsShown()).toEqual(["CASE-C"]);
  fireEvent.change(search, { target: { value: "知らない端末" } });
  expect(rowsShown()).toEqual(["CASE-A"]);
});

test("状態で絞り込む。条件に合う案件がなければ空の表示を出し、条件のクリアで戻る", async () => {
  stubFetch({ "GET /api/cases": { body: ROWS } });
  render(<Harness />);
  await screen.findByRole("table");
  fireEvent.change(screen.getByLabelText("Status"), { target: { value: "Pending data" } });
  expect(rowsShown()).toEqual(["CASE-B"]);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "存在しない語" } });
  expect(screen.getByTestId("empty-list")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(rowsShown()).toEqual(["CASE-A", "CASE-B", "CASE-C"]);
});

test("案件が1件もないときは、空の表示を出す", async () => {
  stubFetch({ "GET /api/cases": { body: [] } });
  render(<Harness />);
  expect(await screen.findByTestId("empty-list")).toBeTruthy();
});

test("取得に失敗したら、エラーを表示する（行は出さない）", async () => {
  stubFetch({ "GET /api/cases": { status: 503 } });
  render(<Harness />);
  const alert = await screen.findByTestId("list-error");
  expect(alert.textContent).toContain("Could not retrieve");
  expect(alert.textContent).toContain("HTTP 503");
  expect(alert.textContent).toContain("busy");
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByRole("searchbox")).toBeNull(); // 取得できていないときは、検索欄を出さない
});
