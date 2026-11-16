// 実行: npm test（Vitest）。設問の追加案のAPI呼び出し（fetchはスタブ）と、失敗の分類を確認する。
import { afterEach, expect, test, vi } from "vitest";
import { ApiError } from "./api";
import { createQuestionProposal, decideQuestionProposal, getDemoQuestions } from "./questionsApi";
import { stubFetch } from "./testing";

afterEach(() => vi.unstubAllGlobals());

const q = [{ id: "A01", text: "設問？" }];

test("一覧: 版と設問を返す", async () => {
  stubFetch({ "GET /api/admin/questions": { body: { revision: 3, questions: q, mock: true, source: { kind: "mock" } } } });
  expect(await getDemoQuestions()).toEqual({ revision: 3, questions: q, source: { kind: "mock" } });
});

test("提案の作成: 依頼をPOSTし、提案ID・Before・After・追加IDを返す", async () => {
  const spy = stubFetch({
    "POST /api/admin/question-proposals": {
      status: 201,
      body: { proposal_id: "p1", before: q, after: [...q, { id: "A02", text: "新？" }], added: { id: "A02", text: "新？" } },
    },
  });
  const result = await createQuestionProposal("依頼");
  expect(result).toEqual({
    proposalId: "p1",
    before: q,
    after: [...q, { id: "A02", text: "新？" }],
    addedId: "A02",
    source: { kind: "mock" }, // 応答に source がなければ、モックとして扱う
  });
  expect(JSON.parse(String(spy.mock.calls[0][1]?.body))).toEqual({ prompt: "依頼" });
});

test("決定: 提案IDをエンコードしてPOSTし、反映の有無と一覧を返す", async () => {
  const spy = stubFetch({
    "POST /api/admin/question-proposals/a%2Fb/decision": { body: { applied: true, revision: 2, questions: q } },
  });
  expect(await decideQuestionProposal("a/b", "yes")).toEqual({ applied: true, revision: 2, questions: q, source: { kind: "mock" } });
  expect(JSON.parse(String(spy.mock.calls[0][1]?.body))).toEqual({ decision: "yes" });
});

test("失敗の分類: 422・502・409・404・その他は、状態と文言を分ける。どれも成功にしない", async () => {
  const cases: [number, string][] = [
    [422, "検証で拒否されました"],
    [502, "AIの応答を得られませんでした"],
    [504, "タイムアウトしました"],
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

test("廃止案の作成: 廃止対象・参照ルール・廃止できるか・既存案件への影響を返す（結果コードは表示名にする）", async () => {
  const retired = { id: "A13", text: "失効？", retired: true };
  stubFetch({
    "POST /api/admin/question-proposals": {
      status: 201,
      body: {
        kind: "retire",
        proposal_id: "p2",
        before: [{ id: "A13", text: "失効？" }],
        after: [retired],
        retired: { id: "A13", text: "失効？" },
        references: [{ rule_id: "R01", revision: 2, result_code: "caution" }],
        can_retire: false,
        impact: {
          unjudged: [{ case_id: "C1", summary: "概要1", answer: "yes" }],
          judged: [{ case_id: "C2", summary: "概要2", answer: null, result_code: "no_issue" }],
        },
        cascade: [],
      },
    },
  });
  expect(await createQuestionProposal("設問A13を廃止して")).toEqual({
    kind: "retire",
    proposalId: "p2",
    before: [{ id: "A13", text: "失効？" }],
    after: [retired],
    retiredId: "A13",
    retiredText: "失効？",
    source: { kind: "mock" },
    references: [{ ruleId: "R01", revision: 2, result: "注意" }],
    canRetire: false,
    impact: {
      unjudged: [{ caseId: "C1", summary: "概要1", answer: "yes" }],
      judged: [{ caseId: "C2", summary: "概要2", answer: null, result: "問題なし" }],
    },
  });
});

test("廃止の拒否（409）は、サーバーの文言を出す。成功にしない", async () => {
  stubFetch({
    "POST /api/admin/question-proposals/p2/decision": {
      status: 409,
      body: { detail: "設問A09は、現行の判定ルール（R01）が参照しているため、廃止できません。設問の一覧は変わっていません" },
    },
  });
  const error = (await decideQuestionProposal("p2", "yes").catch((e: unknown) => e)) as ApiError;
  expect(error.status).toBe(409);
  expect(error.message).toContain("R01");
  expect(error.message).toContain("廃止できません");
});

const bedrock = { kind: "bedrock", model_id: "demo.model-v1:0", region: "region-demo-1" };
const bedrockSource = { kind: "bedrock", modelId: "demo.model-v1:0", region: "region-demo-1" };

test("実接続の生成元: 一覧・追加案・廃止案・決定のどれでも、source を読み取る", async () => {
  stubFetch({
    "GET /api/admin/questions": { body: { revision: 1, questions: q, source: bedrock } },
    "POST /api/admin/question-proposals": (init) =>
      (JSON.parse(String(init?.body)) as { prompt: string }).prompt === "廃止"
        ? {
            status: 201,
            body: {
              kind: "retire",
              proposal_id: "p3",
              before: q,
              after: q,
              retired: q[0],
              references: [],
              can_retire: true,
              impact: { unjudged: [], judged: [] },
              source: bedrock,
            },
          }
        : { status: 201, body: { proposal_id: "p1", before: q, after: q, added: q[0], source: bedrock } },
    "POST /api/admin/question-proposals/p1/decision": { body: { applied: true, revision: 2, questions: q, source: bedrock } },
  });
  expect((await getDemoQuestions()).source).toEqual(bedrockSource);
  expect((await createQuestionProposal("追加")).source).toEqual(bedrockSource);
  expect((await createQuestionProposal("廃止")).source).toEqual(bedrockSource);
  expect((await decideQuestionProposal("p1", "yes")).source).toEqual(bedrockSource);
});

test("失敗の文言: モックの表記を決め打ちせず、サーバーの固定の理由を出す（422・502・504）", async () => {
  const cases: [number, string, string][] = [
    [422, "AIの出力をJSONとして読めませんでした", "AIの応答が検証で拒否されました（AIの出力をJSONとして読めませんでした）"],
    [502, "AIのサービスに接続できませんでした", "AIの応答を得られませんでした（AIのサービスに接続できませんでした）"],
    [504, "AIの応答がタイムアウトしました", "AIの応答がタイムアウトしました（AIの応答がタイムアウトしました）"],
  ];
  for (const [status, detail, expected] of cases) {
    stubFetch({ "POST /api/admin/question-proposals": { status, body: { detail } } });
    const error = (await createQuestionProposal("x").catch((e: unknown) => e)) as ApiError;
    expect(error.message, String(status)).toContain(expected);
    expect(error.message, String(status)).not.toContain("モック");
    expect(error.message, String(status)).toContain("デモの設問一覧は変わっていません");
  }
});
