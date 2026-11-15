import { useState, type FormEvent } from "react";
import { AnswerCounts } from "./AnswerCounts";
import { ApiError, describeError, saveJudgment, type HumanAnswers } from "./api";
import { StatusBadge } from "./Badges";
import { answerLabel, statusOf, tally, type AdoptedRule, type Answer, type ResultName, type ReviewCase } from "./data/cases";
import { questions, type QuestionId } from "./data/questions";
import { RESULTS, canSkipQuestion, matchRules, type AnswerMap, type Rule, type RuleRow } from "./matching";
import { useCase, useRules } from "./useApi";

// 案件はAPIから取得し、［判定する］で、結果と、変更した回答だけを保存する（途中の自動保存はない）。
// 保存するまで、回答の変更は、この画面のメモリ上だけに持つ。開き直すと、最後に保存した値に戻る。

function openState(answers: AnswerMap, rules: readonly Rule[]): Record<string, boolean> {
  return Object.fromEntries(matchRules(answers, rules).map((r) => [r.rule.id, !r.gray]));
}

const backLink = (
  <a className="back" href="#/"><span aria-hidden="true">←</span> 一覧へ戻る</a>
);

export default function CaseDetail({ caseId }: { caseId: string }) {
  const remote = useCase(caseId);
  const rulesRemote = useRules();

  if (remote.status === "loading" || (remote.status === "ok" && rulesRemote.status === "loading")) {
    return (
      <section aria-busy="true">
        {backLink}
        <p role="status" data-testid="case-loading">案件を読み込み中…</p>
      </section>
    );
  }
  if (remote.status === "error" && !(remote.error instanceof ApiError && remote.error.status === 404)) {
    return (
      <section aria-labelledby="err-title">
        {backLink}
        <div className="error" role="alert" data-testid="case-error">
          <h2 id="err-title" tabIndex={-1}>案件を取得できませんでした</h2>
          <p>{describeError(remote.error)}</p>
          <a className="btn" href="#/">一覧へ戻る</a>
        </div>
      </section>
    );
  }
  if (remote.status === "error") {
    return (
      <section aria-labelledby="err-title">
        <a className="back" href="#/"><span aria-hidden="true">←</span> 一覧へ戻る</a>
        <div className="error" role="alert" data-testid="case-not-found">
          <h2 id="err-title" tabIndex={-1}>案件を表示できません</h2>
          <p>案件ID「{caseId}」の案件が見つかりませんでした。一覧から案件を選び直してください。</p>
          <a className="btn" href="#/">一覧へ戻る</a>
        </div>
      </section>
    );
  }
  if (rulesRemote.status === "error") {
    return (
      <section aria-labelledby="err-title">
        {backLink}
        <div className="error" role="alert" data-testid="rules-error">
          <h2 id="err-title" tabIndex={-1}>照合に使うルールを取得できませんでした</h2>
          <p>{describeError(rulesRemote.error)}</p>
          <a className="btn" href="#/">一覧へ戻る</a>
        </div>
      </section>
    );
  }
  if (rulesRemote.status !== "ok") return null; // 読み込み中は上で返している（型の絞り込み用）
  return <Review c={remote.data} rules={rulesRemote.data} save={(result, answers, adopted) => saveJudgment(caseId, result, answers, adopted)} />;
}

export function Review({
  c,
  rules,
  save,
}: {
  c: ReviewCase;
  rules: readonly Rule[];
  save: (result: ResultName, answers: HumanAnswers, adopted: AdoptedRule | null) => Promise<void>;
}) {
  const [answers, setAnswers] = useState<AnswerMap>(c.answers);
  // 保存済みの回答（開いたときの値）。送るのは、これと違う設問だけ。登録は1回だけなので、保存後は更新しない。
  const baseline = c.answers;
  const [edited, setEdited] = useState<ReadonlySet<QuestionId>>(new Set());
  // 行の開閉。初期は食い違わない行を開く。回答が変わったときだけ同じ規則で更新し、手動の開閉はそれまで保つ。
  const [open, setOpen] = useState<Record<string, boolean>>(() => openState(c.answers, rules));
  const [outcome, setOutcome] = useState<ResultName | "">(c.result ?? "");
  // 保存済みの結果（開いたときの値。保存に成功したら更新する）と、今回の保存の状態。
  const [saved, setSaved] = useState<ResultName | "">(c.result ?? "");
  const [justSaved, setJustSaved] = useState<ResultName | "">("");
  // 採用ルール（参考の記録）。保存した値で始める。採用した行が食い違う（グレー）ようになったら外す。
  // 開いた時点で有効（いまのルールの版があり、通常表示）なものだけを持つ。無効なものを持ち続けると、
  // あとの訂正で通常表示に戻ったときに、押していない採用が復活してしまう。
  const [adopted, setAdopted] = useState<AdoptedRule | null>(() => {
    const saved = c.adopted;
    if (saved === undefined) return null;
    const live = matchRules(c.answers, rules).some(
      (r) => r.rule.id === saved.ruleId && r.rule.revision === saved.revision && !r.gray,
    );
    return live ? saved : null;
  });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  // サーバーが「登録済み」（409）を返した。開き直さないかぎり、保存は必ず拒否されるので、押せなくする。
  const [alreadyRecorded, setAlreadyRecorded] = useState(false);

  const rows = matchRules(answers, rules);
  const isAdopted = (rule: Rule) => adopted?.ruleId === rule.id && adopted.revision === rule.revision;
  // 採用が有効なのは、画面に表示している、食い違わない（通常表示の）行だけ。
  const adoptedRow = adopted === null ? undefined : rows.find((r) => isAdopted(r.rule) && !r.gray);
  const effectiveAdopted = adoptedRow === undefined ? null : adopted;
  const matchingCount = rows.filter((r) => !r.gray).length;
  // 廃止済みの設問は、新たな回答の対象外（保存済みの設問文・回答は、下の別の一覧で読み取り専用に表示する）。
  const retired = c.retiredQuestions ?? [];
  const activeQuestions = questions.filter((q) => !retired.some((r) => r.id === q.id));
  // 判定の登録は1回だけ（2026-11-15の合意）。登録済み（開いたときに結果がある、または、ここで保存に成功した）
  // 案件は、保存済みの設問文・回答・結果を見るだけで、編集も再保存もできない。保存中も、操作を止める。
  const locked = saved !== "";
  const frozen = locked || saving;
  const answeredCount = activeQuestions.filter((q) => answers[q.id] === "yes" || answers[q.id] === "no").length;
  const current = { ...c, answers };
  const counts = tally(current);
  const status = statusOf({ result: saved === "" ? undefined : saved, counts });

  const choose = (id: QuestionId, value: "yes" | "no") => {
    if (frozen) return;
    const changed = answers[id] !== value;
    const next = { ...answers, [id]: value };
    setAnswers(next);
    setEdited(new Set(edited).add(id));
    if (changed) setOpen(openState(next, rules));
    // 採用した行が、回答の訂正でグレーになったら、採用は自動で外れる（戻しても、採用し直すまで戻らない）。
    if (adopted !== null) {
      const row = matchRules(next, rules).find((r) => isAdopted(r.rule));
      if (row === undefined || row.gray) setAdopted(null);
    }
  };

  const toggleAdopt = (row: RuleRow) => {
    if (frozen) return;
    // 採用できるのは通常表示の行だけ。グレーの行を採用しようとしたら、採用は外れる。
    if (row.gray || isAdopted(row.rule)) setAdopted(null);
    else setAdopted({ ruleId: row.rule.id, revision: row.rule.revision });
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (outcome === "" || saving || locked || alreadyRecorded) return; // 二重送信しない。登録済みは送らない
    setSaving(true);
    setSaveError("");
    try {
      // 保存済みの値から変わった設問だけを送る（Yes・Noに限る。確認待ちが変わらない設問は送らない）。
      const changed: HumanAnswers = {};
      for (const q of activeQuestions) {
        const value = answers[q.id];
        if ((value === "yes" || value === "no") && baseline[q.id] !== value) changed[q.id] = value;
      }
      await save(outcome, changed, effectiveAdopted);
      setSaved(outcome);
      setJustSaved(outcome);
    } catch (error) {
      // 失敗したときは、保存済みの表示を変えない。
      setJustSaved("");
      if (error instanceof ApiError && error.status === 409) setAlreadyRecorded(true);
      setSaveError(describeError(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section aria-labelledby="detail-title">
      <a className="back" href="#/"><span aria-hidden="true">←</span> 一覧へ戻る</a>

      <div className="detail-head">
        <div>
          <h2 id="detail-title" tabIndex={-1}>{c.summary}<span className="sr-only">（{c.caseId}）</span></h2>
          <p className="sub-row">{c.caseId} ／ user_id {c.userId}</p>
        </div>
        <div className="detail-status">
          <StatusBadge status={status} />
          {saved !== "" && <span className="result">登録済みの結果: {saved}</span>}
        </div>
      </div>

      <div className="inquiry">
        <h3>申告内容</h3>
        <p>{c.inquiry}</p>
      </div>

      <p className="note note-top" data-testid="save-note">
        {locked
          ? "この案件は判定を登録済みです。保存済みの設問文・回答・結果を確認できます（閲覧のみ。回答の変更や再保存はできません）。"
          : "結果と、変更した回答は、［判定する］で保存されます。［判定する］を押さずに離れると、変更は保存されません（開き直すと、最後に保存した値に戻ります）。登録は1回だけで、登録後は変更できません。"}
      </p>

      <div className="columns">
        <section className="pane" aria-labelledby="q-title">
          <div className="pane-head">
            <h3 id="q-title">設問と回答</h3>
            <span role="status" data-testid="progress">{answeredCount}／{activeQuestions.length}問 入力済み</span>
          </div>
          <div className="pane-counts"><AnswerCounts counts={counts} /></div>
          <ol className="question-list">
            {activeQuestions.map((q) => {
              const a: Answer | undefined = answers[q.id];
              const optional = a === undefined && canSkipQuestion(q.id, answers, rules);
              const auto = c.autoQuestions?.includes(q.id) === true && !edited.has(q.id);
              return (
                <li key={q.id} className={`question${optional ? " gray" : ""}${a === "pending" ? " is-pending" : ""}`} data-testid={`q-${q.id}`}>
                  <strong className="q-id">{q.id}</strong>
                  <span className="q-text">
                    {q.prompt}
                    {auto && <span className="origin"><span className="sr-only"> </span>自動</span>}
                    {a === "pending" && <span className="badge ans-pending"><span aria-hidden="true">▲ </span>確認待ち</span>}
                    {a === undefined && <span className="sr-only"> 未回答</span>}
                    {optional && <span className="sr-only">（照合結果を絞り込むための回答は省略できます）</span>}
                  </span>
                  <div className="answers" role="group" aria-label={`${q.id} ${q.prompt}`}>
                    <button type="button" aria-pressed={a === "yes"} disabled={frozen} onClick={() => choose(q.id, "yes")}>Yes</button>
                    <button type="button" aria-pressed={a === "no"} disabled={frozen} onClick={() => choose(q.id, "no")}>No</button>
                  </div>
                  {a === "pending" && (
                    <p className="warn">
                      {c.pendingReasons?.[q.id] ?? "資料を確認できていません。"}
                      Yes・Noのどちらにも数えません。資料で確認できたらYes／Noを選んでください。
                    </p>
                  )}
                </li>
              );
            })}
          </ol>
          {retired.length > 0 && (
            <section className="retired-questions" aria-labelledby="retired-title" data-testid="retired-questions">
              <h4 id="retired-title">廃止済みの設問（保存済みの内容）</h4>
              <p className="hint" data-testid="retired-note">
                {locked
                  ? "登録時の結果と、保存済みの設問文・回答を表示しています。廃止した設問には、新たに回答できません。"
                  : "最新のルールで照合します。廃止した設問は、新たな回答の対象外です。保存済みの設問文と回答は、このまま確認できます。"}
              </p>
              <ul>
                {retired.map((r) => (
                  <li key={r.id} data-testid={`retired-${r.id}`}>
                    <strong className="q-id">{r.id}</strong> {r.prompt}
                    <span className="badge retired-label">廃止済み</span>
                    <span className="retired-answer" data-testid={`retired-answer-${r.id}`}> 保存済みの回答: {answerLabel(r.answer)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </section>

        <section className="pane" aria-labelledby="r-title">
          <div className="pane-head">
            <h3 id="r-title">照合結果</h3>
            <span role="status" data-testid="rule-count">照合 {matchingCount}／{rows.length}件</span>
          </div>
          <div className="rule-list">
            {rows.map((row) => {
              const { rule, gray, states } = row;
              return (
              <details
                key={rule.id}
                className={`rule${gray ? " gray" : ""}`}
                open={open[rule.id] ?? false}
                onToggle={(e) => {
                  const isOpen = e.currentTarget.open;
                  setOpen((prev) => (prev[rule.id] === isOpen ? prev : { ...prev, [rule.id]: isOpen }));
                }}
                data-testid={`rule-${rule.id}`}
                data-display={gray ? "gray" : "normal"}
              >
                <summary>
                  <strong>{rule.id}</strong>{rule.result}
                  {isAdopted(rule) && !gray && <span className="adopted-badge" data-testid={`adopted-${rule.id}`}>採用中</span>}
                  {gray && <span className="sr-only">（入力済みの回答と異なる条件があります）</span>}
                </summary>
                <dl className="conditions">
                  {questions.map((q) => (
                    <div key={q.id} className={states[q.id] === "match" ? "matched" : undefined} data-testid={`cond-${rule.id}-${q.id}`}>
                      <dt>{q.id}</dt>
                      <dd>{states[q.id] === "any" ? "問わない" : rule.expected[q.id] === "yes" ? "Yes" : "No"}{states[q.id] === "match" && <span className="sr-only">（入力済みの回答と一致）</span>}</dd>
                    </div>
                  ))}
                </dl>
                {!gray && !locked && (
                  <div className="rule-actions">
                    <button type="button" className="btn ghost" data-testid={`adopt-${rule.id}`} onClick={() => toggleAdopt(row)}>
                      {isAdopted(rule) ? "採用を外す" : "このルールを採用"}
                    </button>
                  </div>
                )}
              </details>
              );
            })}
          </div>
        </section>
      </div>

      <form className="decision" aria-label="判定結果登録" onSubmit={submit}>
        <fieldset>
          <legend>判定結果</legend>
          <div className="outcomes">
            {RESULTS.map((r) => {
              // 採用ルールの結果に印を付ける。選ぶ結果は変えない（採用は参考の記録）。
              const hinted = adoptedRow?.rule.result === r;
              return (
                <div key={r} className={`outcome${hinted ? " from-rule" : ""}`} data-testid={`outcome-${r}`}>
                  <label>
                    <input
                      type="radio" name="outcome" value={r} required checked={outcome === r}
                      disabled={frozen}
                      aria-describedby={hinted ? "outcome-hint" : undefined}
                      onChange={() => setOutcome(r)}
                    />
                    {r}
                  </label>
                  {hinted && <span className="outcome-hint" id="outcome-hint" data-testid="outcome-hint">採用ルールの結果</span>}
                </div>
              );
            })}
          </div>
        </fieldset>
        {locked ? (
          <p className="note note-inline" data-testid="readonly-note">
            判定は登録済みのため、［判定する］は使えません。結果と回答は、閲覧のみです。
          </p>
        ) : (
          <button className="btn action" type="submit" disabled={saving || alreadyRecorded}>{saving ? "保存中…" : "判定する"}</button>
        )}
        {justSaved !== "" && <p className="saved" role="status" data-testid="registered">保存しました。登録結果: {justSaved}</p>}
        {saveError !== "" && alreadyRecorded && (
          <p className="error-inline" role="alert" data-testid="save-error">
            この案件は、すでに判定が登録されているため、保存できませんでした（{saveError}）。一覧から開き直して、保存済みの内容を確認してください。
          </p>
        )}
        {saveError !== "" && !alreadyRecorded && <p className="error-inline" role="alert" data-testid="save-error">保存できませんでした。{saveError}。もう一度［判定する］を押してください。</p>}
        <p className="note note-inline" data-testid="adopted-note">
          {effectiveAdopted === null
            ? "採用ルール: なし（参考の記録。結果を選んだだけでは記録されません）"
            : `採用ルール: ${effectiveAdopted.ruleId}（版 ${effectiveAdopted.revision}）。参考の記録で、選んだ結果は変わりません。`}
        </p>
        <p className="note note-inline">
          結果は照合にかかわらず、未回答・確認待ちが残っていても選べます。［判定する］は結果を保存します。実際の処置は行いません（未実装）。
        </p>
      </form>

      <p className="note">
        「自動」は表示用の架空データの印で、自動回答の対象設問を確定するものではありません。R01〜R03は受入例用の架空の組み合わせで、業務上の認定ルールではありません。
      </p>
    </section>
  );
}
