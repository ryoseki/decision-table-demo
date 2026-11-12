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
