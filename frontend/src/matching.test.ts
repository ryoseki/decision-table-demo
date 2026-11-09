// 実行: node --test src/matching.test.ts（Node 24以降。型は取り除いて直接実行する）
import assert from "node:assert/strict";
import { test } from "node:test";
import { DEMO_RULES, RESULTS, canSkipQuestion, matchRules, type AnswerMap } from "./matching.ts";

const grays = (a: AnswerMap) => matchRules(a).map((r) => `${r.rule.id}:${r.gray ? "gray" : "normal"}`);

test("受入例1: A01=Yes,A03=Yes → R01・R02は通常、R03はグレー", () => {
  assert.deepEqual(grays({ A01: "yes", A03: "yes" }), ["R01:normal", "R02:normal", "R03:gray"]);
});

test("受入例2: A01をNoに訂正 → R01・R02はグレー、R03は通常", () => {
  assert.deepEqual(grays({ A01: "no", A03: "yes" }), ["R01:gray", "R02:gray", "R03:normal"]);
});

test("受入例3: A01=Yes,A03=No → 全行グレー。それでも4種すべて選べる", () => {
  assert.deepEqual(grays({ A01: "yes", A03: "no" }), ["R01:gray", "R02:gray", "R03:gray"]);
  assert.deepEqual([...RESULTS], ["アカウント削除", "アカウント一時凍結", "注意", "問題なし"]);
});

test("未回答・確認待ちだけでは食い違いにならない", () => {
  assert.deepEqual(grays({}), ["R01:normal", "R02:normal", "R03:normal"]);
  const pending = matchRules({ A01: "pending", A02: "pending", A03: "pending" });
  assert.ok(pending.every((r) => !r.gray));
  assert.equal(pending[0].states.A01, "pending");
  assert.equal(matchRules({}).at(0)?.states.A01, "unanswered");
});

test("確認待ちをNoで解消すると食い違いとして扱う", () => {
  assert.equal(matchRules({ A01: "pending" })[2].gray, false);
  assert.equal(matchRules({ A01: "yes" })[2].gray, true);
});

test("一致する条件はmatch、食い違いはmismatch", () => {
  const r01 = matchRules({ A01: "yes", A06: "yes" })[0];
  assert.equal(r01.states.A01, "match");
  assert.equal(r01.states.A06, "mismatch");
  assert.equal(r01.gray, true);
});

test("左側の不要判定: 食い違わない行が0件なら不要にしない", () => {
  assert.equal(canSkipQuestion("A02", { A01: "yes", A03: "no" }), false);
});

test("左側の不要判定: 結果集合が変わらない設問だけ不要", () => {
  // 全12問が決まった行が1つだけ残る場合、残る行はYes側だけなので不要にならない。
  assert.equal(canSkipQuestion("A12", { A01: "yes", A02: "yes", A03: "yes" }), false);
});

test("左側の不要判定: 他が同じでYes・Noの結果が同じなら不要（任意の規則で確認）", () => {
  const base = DEMO_RULES[0];
  const rules = [
    { ...base, id: "X1", expected: { ...base.expected, A12: "yes" as const } },
    { ...base, id: "X2", expected: { ...base.expected, A12: "no" as const } },
  ];
  assert.equal(canSkipQuestion("A12", {}, rules), true);
  assert.equal(canSkipQuestion("A11", {}, rules), false);
});
