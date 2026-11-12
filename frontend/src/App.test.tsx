// 実行: npm test（Vitest + jsdom）。ハッシュで、一覧と詳細が切り替わることを確認する。
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

beforeEach(() => {
  window.location.hash = "";
  // 画面を切り替えるとき、App は先頭へスクロールする（jsdom は未実装）。
  vi.stubGlobal("scrollTo", vi.fn());
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

test("ハッシュが空なら一覧を表示する", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "審査対象一覧" })).toBeTruthy();
  expect(screen.queryByTestId("preview-note")).toBeNull();
});

test("#/cases/<案件ID> なら、その案件の詳細を表示する", () => {
  window.location.hash = "#/cases/CASE-B";
  render(<App />);
  expect(screen.getByTestId("preview-note")).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "審査対象一覧" })).toBeNull();
});

test("ハッシュを変えると、一覧と詳細が切り替わる", () => {
  render(<App />);
  goTo("#/cases/CASE-B");
  expect(screen.getByTestId("preview-note")).toBeTruthy();
  goTo("#/");
  expect(screen.getByRole("heading", { name: "審査対象一覧" })).toBeTruthy();
});

test("案件を切り替えると、回答の入力状態はリセットされる", () => {
  window.location.hash = "#/cases/CASE-B";
  render(<App />);
  const a01 = () => screen.getByTestId("q-A01").querySelectorAll("button")[0];
  act(() => a01().click());
  expect(a01().getAttribute("aria-pressed")).toBe("true");
  goTo("#/cases/CASE-C"); // CASE-C は A01=Yes で始まる別の案件
  goTo("#/cases/CASE-B");
  expect(a01().getAttribute("aria-pressed")).toBe("false"); // 戻ったら初期状態（未回答）
});
