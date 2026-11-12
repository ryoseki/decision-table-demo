// 実行: npm test（Vitest + jsdom）。ハッシュのルーティングのフックを確認する。
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useRoute } from "./useRoute";

beforeEach(() => {
  window.location.hash = "";
});
afterEach(cleanup);

const changeHash = (hash: string) =>
  act(() => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

test("最初は、今のハッシュの画面を返す", () => {
  window.location.hash = "#/cases/CASE-B";
  const { result } = renderHook(() => useRoute());
  expect(result.current).toEqual({ name: "detail", caseId: "CASE-B" });
});

test("ハッシュが変わる（hashchange）と、画面が更新される", () => {
  const { result } = renderHook(() => useRoute());
  expect(result.current).toEqual({ name: "list" });
  changeHash("#/cases/CASE-D");
  expect(result.current).toEqual({ name: "detail", caseId: "CASE-D" });
  changeHash("#/");
  expect(result.current).toEqual({ name: "list" });
});

test("取り外す（unmount）と、hashchange のリスナーを外す", () => {
  const add = vi.spyOn(window, "addEventListener");
  const remove = vi.spyOn(window, "removeEventListener");
  const { unmount } = renderHook(() => useRoute());
  const added = add.mock.calls.filter(([type]) => type === "hashchange");
  expect(added).toHaveLength(1);
  unmount();
  const removed = remove.mock.calls.filter(([type]) => type === "hashchange");
  expect(removed).toHaveLength(1);
  expect(removed[0][1]).toBe(added[0][1]); // 追加したものと同じリスナーを外す
  add.mockRestore();
  remove.mockRestore();
});
