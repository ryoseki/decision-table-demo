// 実行: npm test（Vitest）。判定ルールの追加案・削除案のAPI呼び出し（APIの形の変換と、エラーの文言）を確認する。
import { afterEach, expect, test, vi } from "vitest";
import { ApiError } from "./api";
import { createRuleProposal, decideRuleProposal, getDemoRules } from "./rulesApi";
import { stubFetch } from "./testing";

afterEach(() => vi.unstubAllGlobals());

const apiRule = { rule_id: "R04", revision: 1, result_code: "no_issue", conditions: { A09: "yes", A10: "no" } };

test("一覧: 結果コードを表示名に、条件を設問IDと回答の並びに変える", async () => {
  stubFetch({ "GET /api/admin/rules": { body: { revision: 3, rules: [apiRule] } } });
  expect(await getDemoRules()).toEqual({
    revision: 3,
    rules: [
      {
        ruleId: "R04",
        revision: 1,
        result: "問題なし",
        conditions: [
          { questionId: "A09", answer: "yes" },
          { questionId: "A10", answer: "no" },
        ],
      },
    ],
    source: { kind: "mock" },
  });
});

test("追加案・削除案: kind と、対象のルールIDを取り出す", async () => {
  stubFetch({
    "POST /api/admin/rule-proposals": (init) => {
      const prompt = (JSON.parse(String(init?.body)) as { prompt: string }).prompt;
      return prompt === "追加"
        ? { status: 201, body: { proposal_id: "p1", kind: "add", before: [], after: [apiRule], added: apiRule } }
        : { status: 201, body: { proposal_id: "p2", kind: "remove", before: [apiRule], after: [], removed: apiRule } };
    },
  });
  const added = await createRuleProposal("追加");
  expect(added).toMatchObject({ kind: "add", proposalId: "p1", targetId: "R04" });
  expect(added.after).toHaveLength(1);
  const removed = await createRuleProposal("削除");
  expect(removed).toMatchObject({ kind: "remove", proposalId: "p2", targetId: "R04" });
  expect(removed.after).toHaveLength(0);
});

test("更新案: kind・対象のルールID・追加する条件（設問IDと回答）を取り出す。追加案・削除案では条件は空", async () => {
  const updated = { ...apiRule, revision: 2 };
  const target = { ...apiRule, conditions: { A10: "no" } };
  stubFetch({
    "POST /api/admin/rule-proposals": {
      status: 201,
      body: { proposal_id: "u1", kind: "update", before: [target], after: [updated], target, updated, added_conditions: { A09: "yes" } },
    },
  });
  const proposal = await createRuleProposal("R04にA09がYesを追加して");
  expect(proposal).toMatchObject({ kind: "update", proposalId: "u1", targetId: "R04" });
  expect(proposal.addedConditions).toEqual([{ questionId: "A09", answer: "yes" }]);
  expect(proposal.before).toHaveLength(1);
  expect(proposal.after).toHaveLength(1); // 新設せず、同じIDの行を置き換える
  expect(proposal.after[0].revision).toBe(2);

  stubFetch({ "POST /api/admin/rule-proposals": { status: 201, body: { proposal_id: "p1", kind: "add", before: [], after: [apiRule], added: apiRule } } });
  expect((await createRuleProposal("追加")).addedConditions).toEqual([]);
});

test.each([
  ["対象(target)がない", { added_conditions: { A09: "yes" } }],
  ["対象のIDが空", { target: { ...apiRule, rule_id: "" }, added_conditions: { A09: "yes" } }],
  ["追加する条件(added_conditions)がない", { target: apiRule }],
  ["追加する条件が空", { target: apiRule, added_conditions: {} }],
  ["追加する条件の回答がyes/noでない", { target: apiRule, added_conditions: { A09: "maybe" } }],
])("更新案の応答が不完全（%s）: 空のIDや空の差分のプレビューにせず、理由つきの例外にする", async (_name, partial) => {
  stubFetch({
    "POST /api/admin/rule-proposals": { status: 201, body: { proposal_id: "u1", kind: "update", before: [apiRule], after: [apiRule], ...partial } },
  });
  const error = await createRuleProposal("R04にA09がYesを追加して").catch((e: unknown) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect((error as ApiError).message).toContain("対象のルールまたは追加する条件を読み取れませんでした");
  expect((error as ApiError).message).toContain("判定ルールの一覧は変わっていません");
});

test("定義にない結果コードは、別の結果に見せず、例外にする", async () => {
  stubFetch({ "GET /api/admin/rules": { body: { revision: 1, rules: [{ ...apiRule, result_code: "unknown" }] } } });
  await expect(getDemoRules()).rejects.toThrow("定義にない結果コード");
});

test("決定: プロポーザルIDをURLに入れ、decision を送る。applied を返す", async () => {
  const spy = stubFetch({ "POST /api/admin/rule-proposals/a%2Fb/decision": { body: { applied: true, revision: 2, rules: [apiRule] } } });
  const result = await decideRuleProposal("a/b", "yes");
  expect(result.applied).toBe(true);
  expect(result.rules[0].ruleId).toBe("R04");
  expect(JSON.parse(String(spy.mock.calls[0][1]?.body))).toEqual({ decision: "yes" });
});

test("エラー: 検証エラー（422）は理由つきで、判定ルールの一覧は変わっていないと伝える", async () => {
  stubFetch({ "POST /api/admin/rule-proposals": { status: 422, body: { detail: "設問A99は存在しません" } } });
  const error = await createRuleProposal("x").catch((e: unknown) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect((error as ApiError).status).toBe(422);
  expect((error as ApiError).message).toContain("設問A99は存在しません");
  expect((error as ApiError).message).toContain("判定ルールの一覧は変わっていません");
  expect((error as ApiError).message).not.toContain("設問一覧");
});

test("エラー: 提案の作成の通信エラーは「変わっていない」と言い、決定の通信エラーは言い切らない", async () => {
  stubFetch({
    "POST /api/admin/rule-proposals": new Error("down"),
    "POST /api/admin/rule-proposals/p1/decision": new Error("down"),
  });
  const create = (await createRuleProposal("x").catch((e: unknown) => e)) as ApiError;
  expect(create.message).toContain("判定ルールの一覧は変わっていません");
  const decide = (await decideRuleProposal("p1", "yes").catch((e: unknown) => e)) as ApiError;
  expect(decide.message).toContain("確認できていません");
  expect(decide.message).not.toContain("変わっていません");
});

test("エラー: 409は詳細を使い、404は作り直しを案内する", async () => {
  stubFetch({
    "POST /api/admin/rule-proposals/p1/decision": { status: 409, body: { detail: "判定ルールの一覧が提案の後に変わったため、反映しませんでした" } },
    "POST /api/admin/rule-proposals/p2/decision": { status: 404, body: { detail: "提案がありません（未知、または処理済みです）" } },
  });
  expect(((await decideRuleProposal("p1", "yes").catch((e: unknown) => e)) as ApiError).message).toContain("最新の一覧を表示しています");
  const notFound = ((await decideRuleProposal("p2", "yes").catch((e: unknown) => e)) as ApiError).message;
  expect(notFound).toContain("もう一度、案を作ってください");
  expect(notFound).not.toContain("追加案");
});

test("生成元: 一覧と提案から source を読む。実接続はモデルとリージョンを持ち、欠落はモック扱い", async () => {
  const source = { kind: "bedrock", model_id: "demo.model-v1:0", region: "region-demo-1" };
  stubFetch({
    "GET /api/admin/rules": { body: { revision: 1, rules: [apiRule], source } },
    "POST /api/admin/rule-proposals": {
      status: 201,
      body: { proposal_id: "p1", kind: "add", before: [], after: [apiRule], added: apiRule, source },
    },
  });
  const expected = { kind: "bedrock", modelId: "demo.model-v1:0", region: "region-demo-1" };
  expect((await getDemoRules()).source).toEqual(expected);
  expect((await createRuleProposal("追加")).source).toEqual(expected);
  stubFetch({ "POST /api/admin/rule-proposals": { status: 201, body: { proposal_id: "p1", kind: "add", before: [], after: [], added: apiRule } } });
  expect((await createRuleProposal("追加")).source).toEqual({ kind: "mock" });
});

test("失敗の文言（504・502）: 判定ルールの一覧は変わっていないと伝え、モックとは言わない", async () => {
  for (const [status, detail] of [[504, "AIの応答がタイムアウトしました"], [502, "AIのサービスに接続できませんでした"]] as const) {
    stubFetch({ "POST /api/admin/rule-proposals": { status, body: { detail } } });
    const error = (await createRuleProposal("x").catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(status);
    expect(error.message).toContain(detail);
    expect(error.message).toContain("判定ルールの一覧は変わっていません");
    expect(error.message).not.toContain("モック");
  }
});
