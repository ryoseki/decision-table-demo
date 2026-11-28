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
  <a className="back" href="#/"><span aria-hidden="true">←</span> Back to list</a>
);

export default function CaseDetail({ caseId }: { caseId: string }) {
  const remote = useCase(caseId);
  const rulesRemote = useRules();

  if (remote.status === "loading" || (remote.status === "ok" && rulesRemote.status === "loading")) {
    return (
      <section aria-busy="true">
        {backLink}
        <p role="status" data-testid="case-loading">Loading case…</p>
      </section>
    );
  }
  if (remote.status === "error" && !(remote.error instanceof ApiError && remote.error.status === 404)) {
    return (
      <section aria-labelledby="err-title">
        {backLink}
        <div className="error" role="alert" data-testid="case-error">
          <h2 id="err-title" tabIndex={-1}>Could not retrieve the case</h2>
          <p>{describeError(remote.error)}</p>
          <a className="btn" href="#/">Back to list</a>
        </div>
      </section>
    );
  }
  if (remote.status === "error") {
    return (
      <section aria-labelledby="err-title">
        <a className="back" href="#/"><span aria-hidden="true">←</span> Back to list</a>
        <div className="error" role="alert" data-testid="case-not-found">
          <h2 id="err-title" tabIndex={-1}>Cannot display the case</h2>
          <p>No case with case ID "{caseId}" was found. Choose a case from the list again.</p>
          <a className="btn" href="#/">Back to list</a>
        </div>
      </section>
    );
  }
  if (rulesRemote.status === "error") {
    return (
      <section aria-labelledby="err-title">
        {backLink}
        <div className="error" role="alert" data-testid="rules-error">
          <h2 id="err-title" tabIndex={-1}>Could not retrieve the rules used for matching</h2>
          <p>{describeError(rulesRemote.error)}</p>
          <a className="btn" href="#/">Back to list</a>
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
      <a className="back" href="#/"><span aria-hidden="true">←</span> Back to list</a>

      <div className="detail-head">
        <div>
          <h2 id="detail-title" tabIndex={-1}>{c.summary} <span className="sr-only">({c.caseId})</span></h2>
          <p className="sub-row">{c.caseId} / user_id {c.userId}</p>
        </div>
        <div className="detail-status">
          <StatusBadge status={status} />
          {saved !== "" && <span className="result">Registered result: {saved}</span>}
        </div>
      </div>

      <div className="inquiry">
        <h3>Reported issue</h3>
        <p>{c.inquiry}</p>
      </div>

      <p className="note note-top" data-testid="save-note">
        {locked
          ? "A judgment has already been registered for this case. You can view the saved question text, answers, and result (read-only; answers cannot be changed or saved again)."
          : "The result and any changed answers are saved with [Submit judgment]. If you leave without pressing [Submit judgment], your changes are not saved (reopening restores the last saved values). Registration happens only once and cannot be changed afterward."}
      </p>

      <div className="columns">
        <section className="pane" aria-labelledby="q-title">
          <div className="pane-head">
            <h3 id="q-title">Questions and answers</h3>
            <span role="status" data-testid="progress">{answeredCount} of {activeQuestions.length} answered</span>
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
                    {auto && <span className="origin"><span className="sr-only"> </span>Auto</span>}
                    {a === "pending" && <span className="badge ans-pending"><span aria-hidden="true">▲ </span>Pending</span>}
                    {a === undefined && <span className="sr-only"> Unanswered</span>}
                    {optional && <span className="sr-only"> (An answer to narrow the matching results can be skipped)</span>}
                  </span>
                  <div className="answers" role="group" aria-label={`${q.id} ${q.prompt}`}>
                    <button type="button" aria-pressed={a === "yes"} disabled={frozen} onClick={() => choose(q.id, "yes")}>Yes</button>
                    <button type="button" aria-pressed={a === "no"} disabled={frozen} onClick={() => choose(q.id, "no")}>No</button>
                  </div>
                  {a === "pending" && (
                    <p className="warn">
                      {c.pendingReasons?.[q.id] ?? "The materials could not be confirmed."}
                      It is counted as neither Yes nor No. Once you have confirmed it from the materials, choose Yes or No.
                    </p>
                  )}
                </li>
              );
            })}
          </ol>
          {retired.length > 0 && (
            <section className="retired-questions" aria-labelledby="retired-title" data-testid="retired-questions">
              <h4 id="retired-title">Deactivated questions (saved content)</h4>
              <p className="hint" data-testid="retired-note">
                {locked
                  ? "Showing the result at registration and the saved question text and answers. Deactivated questions cannot be answered anew."
                  : "Matching uses the latest rules. Deactivated questions are no longer open to new answers. You can still view the saved question text and answers."}
              </p>
              <ul>
                {retired.map((r) => (
                  <li key={r.id} data-testid={`retired-${r.id}`}>
                    <strong className="q-id">{r.id}</strong> {r.prompt}
                    <span className="badge retired-label">Deactivated</span>
                    <span className="retired-answer" data-testid={`retired-answer-${r.id}`}> Saved answer: {answerLabel(r.answer)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </section>

        <section className="pane" aria-labelledby="r-title">
          <div className="pane-head">
            <h3 id="r-title">Matching results</h3>
            <span role="status" data-testid="rule-count">{matchingCount} of {rows.length} rules match</span>
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
                  {isAdopted(rule) && !gray && <span className="adopted-badge" data-testid={`adopted-${rule.id}`}>Adopted</span>}
                  {gray && <span className="sr-only"> (Some conditions differ from the answers entered)</span>}
                </summary>
                <dl className="conditions">
                  {questions.map((q) => (
                    <div key={q.id} className={states[q.id] === "match" ? "matched" : undefined} data-testid={`cond-${rule.id}-${q.id}`}>
                      <dt>{q.id}</dt>
                      <dd>{states[q.id] === "any" ? "Not required" : rule.expected[q.id] === "yes" ? "Yes" : "No"}{states[q.id] === "match" && <span className="sr-only"> (matches the answer entered)</span>}</dd>
                    </div>
                  ))}
                  {Object.entries(rule.expected).filter(([id]) => !questions.some((q) => q.id === id))
                    .map(([id, expected]) => (
                      <div key={id} data-testid={`cond-${rule.id}-${id}`}>
                        <dt>{id}</dt>
                        <dd>{expected === "yes" ? "Yes" : "No"} (no such question in this case)</dd>
                      </div>
                    ))}
                </dl>
                {!gray && !locked && (
                  <div className="rule-actions">
                    <button type="button" className="btn ghost" data-testid={`adopt-${rule.id}`} onClick={() => toggleAdopt(row)}>
                      {isAdopted(rule) ? "Unadopt this rule" : "Adopt this rule"}
                    </button>
                  </div>
                )}
              </details>
              );
            })}
          </div>
        </section>
      </div>

      <form className="decision" aria-label="Submit judgment" onSubmit={submit}>
        <fieldset>
          <legend>Judgment result</legend>
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
                  {hinted && <span className="outcome-hint" id="outcome-hint" data-testid="outcome-hint">Result of the adopted rule</span>}
                </div>
              );
            })}
          </div>
        </fieldset>
        {locked ? (
          <p className="note note-inline" data-testid="readonly-note">
            The judgment is already registered, so [Submit judgment] is unavailable. The result and answers are read-only.
          </p>
        ) : (
          <button className="btn action" type="submit" disabled={saving || alreadyRecorded}>{saving ? "Saving…" : "Submit judgment"}</button>
        )}
        {justSaved !== "" && <p className="saved" role="status" data-testid="registered">Saved. Registered result: {justSaved}</p>}
        {saveError !== "" && alreadyRecorded && (
          <p className="error-inline" role="alert" data-testid="save-error">
            This case could not be saved because a judgment has already been registered. Reopen it from the list to check the saved content.
          </p>
        )}
        {saveError !== "" && !alreadyRecorded && <p className="error-inline" role="alert" data-testid="save-error">Could not save. {saveError}. Press [Submit judgment] again.</p>}
        <p className="note note-inline" data-testid="adopted-note">
          {effectiveAdopted === null
            ? "Adopted rule: none (a reference record only; choosing a result does not create one)"
            : `Adopted rule: ${effectiveAdopted.ruleId} (revision ${effectiveAdopted.revision}). This is a reference record only and does not change the chosen result.`}
        </p>
        <p className="note note-inline">
          You can choose a result regardless of matching, even if unanswered or pending questions remain. [Submit judgment] saves the result. No actual action is taken (not implemented).
        </p>
      </form>

      <p className="note">
        "Auto" is a marker on fictional display data and does not fix which questions are answered automatically. R01–R03 are fictional combinations for acceptance examples, not business determination rules.
      </p>
    </section>
  );
}
