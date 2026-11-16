// 判定ルールの追加案・削除案（管理者AIの試作。モック・DBなし）のAPI呼び出し。Reactに依存しない。
// 失敗は ApiError にそろえる（設問の追加案と同じ。文言の対象だけ「判定ルールの一覧」にする）。
import { call } from "./questionsApi";
import { resultNameOf } from "./resultCodes";
import type { ResultName } from "./data/cases";
import { toSource, type GenerationSource } from "./generationSource";

/** 条件は、設問IDと、Yes／No（API の並びのまま）。 */
export type RuleCondition = { questionId: string; answer: "yes" | "no" };
export type DemoRuleRow = { ruleId: string; revision: number; result: ResultName; conditions: RuleCondition[] };
export type DemoRuleList = { revision: number; rules: DemoRuleRow[]; source: GenerationSource };
export type RuleProposal = {
  /** add は追加案、remove は削除案。 */
  kind: "add" | "remove";
  proposalId: string;
  before: DemoRuleRow[];
  after: DemoRuleRow[];
  /** 追加する、または削除するルールのID。 */
  targetId: string;
  /** この提案を作った生成元（モックか、実接続か）。 */
  source: GenerationSource;
};
export type RuleDecision = "yes" | "no";
export type RuleDecisionResult = DemoRuleList & { applied: boolean };

const SUBJECT = "判定ルールの一覧";
// 提案は追加案と削除案の両方がある。404の案内で「追加案」と決め打ちしない。
const PROPOSAL_NAME = "案";
const NETWORK_UNCHANGED = `サーバーに接続できませんでした。${SUBJECT}は変わっていません。`;
const NETWORK_UNKNOWN =
  "サーバーに接続できませんでした。反映されたかどうかを確認できていません。もう一度送るか、一覧を再読み込みして確認してください。";

type ApiRule = { rule_id: string; revision: number; result_code: string; conditions: Record<string, "yes" | "no"> };
type ApiList = { revision: number; rules: ApiRule[]; source?: unknown };

const toRow = (r: ApiRule): DemoRuleRow => ({
  ruleId: r.rule_id,
  revision: r.revision,
  result: resultNameOf(r.result_code),
  conditions: Object.entries(r.conditions).map(([questionId, answer]) => ({ questionId, answer })),
});
const toList = (b: ApiList): DemoRuleList => ({ revision: b.revision, rules: b.rules.map(toRow), source: toSource(b.source) });

export async function getDemoRules(): Promise<DemoRuleList> {
  return toList((await call("/api/admin/rules", undefined, NETWORK_UNCHANGED, SUBJECT, PROPOSAL_NAME)) as ApiList);
}

export async function createRuleProposal(prompt: string): Promise<RuleProposal> {
  const b = (await call(
    "/api/admin/rule-proposals",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt }) },
    NETWORK_UNCHANGED,
    SUBJECT,
    PROPOSAL_NAME,
  )) as {
    proposal_id: string;
    kind: "add" | "remove";
    before: ApiRule[];
    after: ApiRule[];
    added?: ApiRule;
    removed?: ApiRule;
    source?: unknown;
  };
  return {
    kind: b.kind,
    proposalId: b.proposal_id,
    before: b.before.map(toRow),
    after: b.after.map(toRow),
    targetId: (b.kind === "add" ? b.added : b.removed)?.rule_id ?? "",
    source: toSource(b.source),
  };
}

export async function decideRuleProposal(proposalId: string, decision: RuleDecision): Promise<RuleDecisionResult> {
  const b = (await call(
    `/api/admin/rule-proposals/${encodeURIComponent(proposalId)}/decision`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }) },
    NETWORK_UNKNOWN,
    SUBJECT,
    PROPOSAL_NAME,
  )) as ApiList & { applied: boolean };
  return { ...toList(b), applied: b.applied };
}
