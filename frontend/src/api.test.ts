// 実行: npm test（Vitest）。APIの呼び出しを、fetch のスタブで確認する（サーバーに接続しない）。
import { afterEach, expect, test, vi } from "vitest";
import { ApiError, describeError, getCase, listCases, listRules, saveJudgment } from "./api";
import { apiCase, apiRow, stubFetch } from "./testing";

afterEach(() => vi.unstubAllGlobals());

const allConditions = (v: "yes" | "no"): Record<string, string> =>
  Object.fromEntries(Array.from({ length: 12 }, (_, i) => [`A${String(i + 1).padStart(2, "0")}`, v]));

test("一覧: 行を、画面の形に変換する。結果コードは表示名にする。並びは返された順", async () => {
  stubFetch({
    "GET /api/cases": {
      body: [apiRow({ case_id: "CASE-A" }), apiRow({ case_id: "CASE-B", result_code: "caution" })],
    },
  });
  const rows = await listCases();
  expect(rows.map((r) => r.caseId)).toEqual(["CASE-A", "CASE-B"]);
  expect(rows[0]).toEqual({
    caseId: "CASE-A",
    userId: "456",
    summary: "架空の概要",
    inquiry: "架空の本文",
    counts: { yes: 1, no: 0, pending: 0, unanswered: 11 },
  });
  expect(rows[0]).not.toHaveProperty("result"); // 未判定は、結果なし
  expect(rows[1].result).toBe("注意");
});

test("詳細: 回答のある設問だけを answers にする（未回答は入れない）。結果を表示名にする", async () => {
  stubFetch({ "GET /api/cases/CASE-X": { body: apiCase({ result: { result_code: "no_issue" } }) } });
  const c = await getCase("CASE-X");
  expect(c).toMatchObject({ caseId: "CASE-X", userId: "456", summary: "架空の概要", inquiry: "架空の本文" });
  expect(c.answers).toEqual({ A09: "yes" });
  expect(c.result).toBe("問題なし");
});

test("詳細: 案件IDは、URLエンコードして送る", async () => {
  const spy = stubFetch({ "GET /api/cases/CASE%201%2F2": { body: apiCase() } });
  await getCase("CASE 1/2");
  expect(spy).toHaveBeenCalledTimes(1);
});

test("詳細: 確認待ちの回答を、そのまま pending にする", async () => {
  const questions = [{ question_code: "A09", answer: "pending" }];
  stubFetch({ "GET /api/cases/CASE-X": { body: apiCase({ questions }) } });
  expect((await getCase("CASE-X")).answers).toEqual({ A09: "pending" });
});

test("詳細: 404 と 500 は ApiError にし、statusを保つ", async () => {
  stubFetch({ "GET /api/cases/NONE": { status: 404, body: { detail: "案件がありません" } } });
  await expect(getCase("NONE")).rejects.toMatchObject({ name: "ApiError", status: 404 });
  stubFetch({ "GET /api/cases/BAD": { status: 500 } });
  await expect(getCase("BAD")).rejects.toMatchObject({ status: 500 });
});

test("通信できないときは、status 0 の ApiError にする", async () => {
  stubFetch({ "GET /api/cases": new TypeError("fetch failed") });
  const error = await listCases().catch((e: unknown) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({ status: 0 });
  expect(describeError(error)).toContain("接続できませんでした");
});

test("定義にない結果コードが返ってきたら、例外にする", async () => {
  stubFetch({ "GET /api/cases": { body: [apiRow({ result_code: "unknown" })] } });
  await expect(listCases()).rejects.toThrow("定義にない結果コード");
});

test("保存: PUT で、結果コードだけを送る（回答を渡さないとき）", async () => {
  const spy = stubFetch({ "PUT /api/cases/CASE-X/judgment": { status: 204 } });
  await saveJudgment("CASE-X", "アカウント一時凍結");
  const init = spy.mock.calls[0][1] as RequestInit;
  expect(init.method).toBe("PUT");
  expect(init.headers).toEqual({ "Content-Type": "application/json" });
  expect(JSON.parse(init.body as string)).toEqual({ result_code: "account_suspension" }); // 回答は送らない
});

test("保存: 422 などの拒否は ApiError にする", async () => {
  stubFetch({ "PUT /api/cases/CASE-X/judgment": { status: 422, body: { detail: "x" } } });
  await expect(saveJudgment("CASE-X", "注意")).rejects.toMatchObject({ status: 422 });
});

test("ApiError でないものは、一般の文言にする", () => {
  expect(describeError(new Error("内部の詳細"))).toBe("予期しないエラーが発生しました");
});

test("保存: 変更した回答があれば、answers として一緒に送る", async () => {
  const spy = stubFetch({ "PUT /api/cases/CASE-X/judgment": { status: 204 } });
  await saveJudgment("CASE-X", "問題なし", { A09: "no", A03: "yes" });
  const init = spy.mock.calls[0][1] as RequestInit;
  expect(JSON.parse(init.body as string)).toEqual({ result_code: "no_issue", answers: { A09: "no", A03: "yes" } });
});

test("ルール: APIの行を、照合用の形（id・結果の表示名・12問の期待値）にする。順はAPIのまま", async () => {
  stubFetch({
    "GET /api/rules": {
      body: [
        { rule_id: "R01", revision: 2, result_code: "caution", conditions: allConditions("yes") },
        { rule_id: "R02", revision: 1, result_code: "account_deletion", conditions: allConditions("no") },
      ],
    },
  });
  const rules = await listRules();
  expect(rules.map((r) => [r.id, r.result])).toEqual([["R01", "注意"], ["R02", "アカウント削除"]]);
  expect(Object.keys(rules[0].expected)).toHaveLength(12);
  expect(rules[0].expected.A12).toBe("yes");
  expect(rules[1].expected.A01).toBe("no");
});

test("ルール: 12問の条件がそろわないルールは、黙って使わず、エラーにする", async () => {
  const conditions = allConditions("yes");
  delete conditions.A05;
  stubFetch({ "GET /api/rules": { body: [{ rule_id: "R09", revision: 1, result_code: "caution", conditions }] } });
  await expect(listRules()).rejects.toThrow(/R09.*A05/);
});

test("ルール: 定義にない結果コードは、エラーにする", async () => {
  stubFetch({ "GET /api/rules": { body: [{ rule_id: "R09", revision: 1, result_code: "demo_result", conditions: allConditions("yes") }] } });
  await expect(listRules()).rejects.toThrow(/demo_result/);
});

test("ルール: 500 は ApiError にし、statusを保つ", async () => {
  stubFetch({ "GET /api/rules": { status: 500 } });
  await expect(listRules()).rejects.toMatchObject({ name: "ApiError", status: 500 });
});
