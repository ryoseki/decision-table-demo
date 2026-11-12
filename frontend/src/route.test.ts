// 実行: npm test（Vitest）。ハッシュのルーティングの、純粋な関数を確認する。
import { expect, test } from "vitest";
import { detailHref, parseHash } from "./route";

test("#/ と空のハッシュは一覧", () => {
  expect(parseHash("#/")).toEqual({ name: "list" });
  expect(parseHash("")).toEqual({ name: "list" });
});

test("#/cases/<案件ID> は詳細。末尾のスラッシュは許す", () => {
  expect(parseHash("#/cases/CASE-B")).toEqual({ name: "detail", caseId: "CASE-B" });
  expect(parseHash("#/cases/CASE-B/")).toEqual({ name: "detail", caseId: "CASE-B" });
});

test("案件IDはURLデコードする。デコードできない文字列はそのまま使う", () => {
  expect(parseHash("#/cases/CASE%20X")).toEqual({ name: "detail", caseId: "CASE X" });
  expect(parseHash("#/cases/%E0%A4%A")).toEqual({ name: "detail", caseId: "%E0%A4%A" });
});

test("形式の違うハッシュは一覧に戻す", () => {
  for (const hash of ["#/cases/", "#/cases/A/B", "#/other", "#cases/A", "#/cases"]) {
    expect(parseHash(hash), hash).toEqual({ name: "list" });
  }
});

test("detailHref は案件IDをエンコードし、parseHash で元に戻る", () => {
  expect(detailHref("CASE-B")).toBe("#/cases/CASE-B");
  const id = "CASE 日本語/1";
  expect(parseHash(detailHref(id))).toEqual({ name: "detail", caseId: id }); // 「/」を含むIDも、エンコードされ、1つのIDとして戻る
});
