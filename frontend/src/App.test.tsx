// 実行: npm test（Vitest + jsdom）。ハッシュで、一覧と詳細が切り替わることを確認する。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";
import { apiCase, apiRow, stubFetch } from "./testing";

beforeEach(() => {
  window.location.hash = "";
  // 画面を切り替えるとき、App は先頭へスクロールする（jsdom は未実装）。
  vi.stubGlobal("scrollTo", vi.fn());
  stubFetch({
    "GET /api/cases": { body: [apiRow({ case_id: "CASE-B" }), apiRow({ case_id: "CASE-C" })] },
    "GET /api/cases/CASE-B": { body: apiCase({ case_id: "CASE-B" }) },
    "GET /api/cases/CASE-C": {
      body: apiCase({
        case_id: "CASE-C",
        questions: [{ question_code: "A01", answer: "yes" }],
      }),
    },
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const goTo = (hash: string) =>
  act(() => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

test("ハッシュが空なら一覧を表示する（APIから取得した案件を）", async () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "審査対象一覧" })).toBeTruthy();
  expect(await screen.findByText("CASE-B")).toBeTruthy();
  expect(screen.queryByTestId("save-note")).toBeNull();
});

test("#/cases/<案件ID> なら、その案件の詳細を表示する", async () => {
  window.location.hash = "#/cases/CASE-B";
  render(<App />);
  expect(await screen.findByTestId("save-note")).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "審査対象一覧" })).toBeNull();
});

test("ハッシュを変えると、一覧と詳細が切り替わる", async () => {
  render(<App />);
  await screen.findByText("CASE-B");
  goTo("#/cases/CASE-B");
  expect(await screen.findByTestId("save-note")).toBeTruthy();
  goTo("#/");
  expect(screen.getByRole("heading", { name: "審査対象一覧" })).toBeTruthy();
  expect(await screen.findByText("CASE-B")).toBeTruthy();
});

test("ヘッダーの「設問管理」から設問管理（デモ）へ入り、「案件一覧」で戻れる", async () => {
  render(<App />);
  await screen.findByText("CASE-B");
  const link = screen.getByRole("link", { name: "設問管理" });
  expect(link.getAttribute("href")).toBe("#/admin/questions");
  stubFetch({ "GET /api/admin/questions": { body: { revision: 1, questions: [{ id: "A01", text: "設問？" }] } } });
  goTo("#/admin/questions");
  expect(screen.getByRole("heading", { name: "設問管理（デモ）" })).toBeTruthy();
  expect(await screen.findByText("設問？")).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "審査対象一覧" })).toBeNull();
  expect(screen.getByRole("link", { name: "案件一覧" }).getAttribute("href")).toBe("#/");
});

test("設問管理で作った未承認の追加案は、画面を切り替えて戻ると破棄され、現行の一覧は変わらない", async () => {
  const q = Array.from({ length: 12 }, (_, i) => ({ id: `A${String(i + 1).padStart(2, "0")}`, text: `設問${i + 1}？` }));
  const added = { id: "A13", text: "追加の設問？" };
  const spy = stubFetch({
    "GET /api/cases": { body: [apiRow({ case_id: "CASE-B" })] },
    "GET /api/admin/questions": { body: { revision: 1, questions: q } },
    "POST /api/admin/question-proposals": {
      status: 201,
      body: { proposal_id: "p1", before: q, after: [...q, added], added },
    },
  });
  render(<App />);
  await screen.findByText("CASE-B");
  goTo("#/admin/questions");
  await screen.findByText("設問12？");
  fireEvent.change(screen.getByLabelText("変更の依頼（自然文）"), { target: { value: "依頼" } });
  fireEvent.click(screen.getByRole("button", { name: "追加案を作る" }));
  await screen.findByTestId("proposal");

  goTo("#/"); // 承認せずに別の画面へ
  await screen.findByText("CASE-B");
  expect(screen.queryByTestId("proposal")).toBeNull();
  goTo("#/admin/questions");
  await screen.findByText("設問12？");
  expect(screen.queryByTestId("proposal")).toBeNull();
  expect(screen.queryByText("追加の設問？")).toBeNull();
  // 決定は一度も送っていない。
  expect(spy.mock.calls.some(([url]) => String(url).endsWith("/decision"))).toBe(false);
});

test("詳細から一覧へ戻っても、検索語は保たれる", async () => {
  render(<App />);
  await screen.findByText("CASE-B");
  const search = screen.getByRole("searchbox") as HTMLInputElement;
  act(() => {
    fireEvent.change(search, { target: { value: "case-c" } });
  });
  expect(screen.queryByText("CASE-B")).toBeNull();
  goTo("#/cases/CASE-C");
  await screen.findByTestId("save-note");
  goTo("#/");
  await screen.findByText("CASE-C");
  expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("case-c");
  expect(screen.queryByText("CASE-B")).toBeNull();
});

test("案件を切り替えると、回答の入力状態はリセットされる", async () => {
  window.location.hash = "#/cases/CASE-B";
  render(<App />);
  await screen.findByTestId("save-note");
  const a01 = () => screen.getByTestId("q-A01").querySelectorAll("button")[0];
  act(() => a01().click());
  expect(a01().getAttribute("aria-pressed")).toBe("true");
  goTo("#/cases/CASE-C"); // CASE-C は A01=Yes で始まる別の案件
  await screen.findByTestId("save-note");
  goTo("#/cases/CASE-B");
  await screen.findByTestId("save-note");
  expect(a01().getAttribute("aria-pressed")).toBe("false"); // 戻ったら初期状態（未回答）
});
