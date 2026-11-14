// 実行: npm test（Vitest）。設問の追加案のAPI呼び出し（fetchはスタブ）と、失敗の分類を確認する。
import { afterEach, expect, test, vi } from "vitest";
import { ApiError } from "./api";
import { createQuestionProposal, decideQuestionProposal, getDemoQuestions } from "./questionsApi";
import { stubFetch } from "./testing";

afterEach(() => vi.unstubAllGlobals());

const q = [{ id: "A01", text: "設問？" }];

test("一覧: 版と設問を返す", async () => {
  stubFetch({ "GET /api/admin/questions": { body: { revision: 3, questions: q, mock: true } } });
  expect(await getDemoQuestions()).toEqual({ revision: 3, questions: q });
});

test("提案の作成: 依頼をPOSTし、提案ID・Before・After・追加IDを返す", async () => {
  const spy = stubFetch({
    "POST /api/admin/question-proposals": {
      status: 201,
      body: { proposal_id: "p1", before: q, after: [...q, { id: "A02", text: "新？" }], added: { id: "A02", text: "新？" } },
    },
  });
  const result = await createQuestionProposal("依頼");
  expect(result).toEqual({ proposalId: "p1", before: q, after: [...q, { id: "A02", text: "新？" }], addedId: "A02" });
  expect(JSON.parse(String(spy.mock.calls[0][1]?.body))).toEqual({ prompt: "依頼" });
});

test("決定: 提案IDをエンコードしてPOSTし、反映の有無と一覧を返す", async () => {
  const spy = stubFetch({
    "POST /api/admin/question-proposals/a%2Fb/decision": { body: { applied: true, revision: 2, questions: q } },
  });
  expect(await decideQuestionProposal("a/b", "yes")).toEqual({ applied: true, revision: 2, questions: q });
  expect(JSON.parse(String(spy.mock.calls[0][1]?.body))).toEqual({ decision: "yes" });
});

test("失敗の分類: 422・502・409・404・その他は、状態と文言を分ける。どれも成功にしない", async () => {
  const cases: [number, string][] = [
    [422, "検証で拒否されました"],
    [502, "AIの応答を得られませんでした"],
    [409, "最新の一覧を表示しています"],
    [404, "もう一度、追加案を作ってください"],
    [500, "HTTP 500"],
  ];
  for (const [status, text] of cases) {
    stubFetch({ "POST /api/admin/question-proposals": { status, body: { detail: "理由" } } });
    const error = await createQuestionProposal("x").catch((e: unknown) => e);
    expect(error, String(status)).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(status);
    expect((error as ApiError).message, String(status)).toContain(text);
  }
});

test("通信できない場合は status 0 の ApiError", async () => {
  stubFetch({ "GET /api/admin/questions": new Error("down") });
  const error = await getDemoQuestions().catch((e: unknown) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect((error as ApiError).status).toBe(0);
});

test("決定の通信エラーは、反映の有無を言い切らない。提案の作成の通信エラーは、変わっていないと言える", async () => {
  stubFetch({ "POST /api/admin/question-proposals/p1/decision": new Error("down") });
  const decision = await decideQuestionProposal("p1", "no").catch((e: unknown) => e);
  expect((decision as ApiError).status).toBe(0);
  expect((decision as ApiError).message).toContain("確認できていません");
  expect((decision as ApiError).message).not.toContain("変わっていません");

  stubFetch({ "POST /api/admin/question-proposals": new Error("down") });
  const created = await createQuestionProposal("x").catch((e: unknown) => e);
  expect((created as ApiError).message).toContain("変わっていません");
});
