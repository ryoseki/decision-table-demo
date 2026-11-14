import { useState, type FormEvent } from "react";
import { AnswerCounts } from "./AnswerCounts";
import { ApiError, describeError, saveJudgment, type HumanAnswers } from "./api";
import { StatusBadge } from "./Badges";
import { statusOf, tally, type Answer, type ResultName, type ReviewCase } from "./data/cases";
import { questions, type QuestionId } from "./data/questions";
import { RESULTS, canSkipQuestion, matchRules, type AnswerMap, type Rule } from "./matching";
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
  return <Review c={remote.data} rules={rulesRemote.data} save={(result, answers) => saveJudgment(caseId, result, answers)} />;
}

export function Review({
  c,
  rules,
  save,
}: {
  c: ReviewCase;
  rules: readonly Rule[];
  save: (result: ResultName, answers: HumanAnswers) => Promise<void>;
}) {
  const [answers, setAnswers] = useState<AnswerMap>(c.answers);
  // 保存済みの回答（開いたときの値。保存に成功したら更新する）。送るのは、これと違う設問だけ。
  const [baseline, setBaseline] = useState<AnswerMap>(c.answers);
  const [edited, setEdited] = useState<ReadonlySet<QuestionId>>(new Set());
  // 行の開閉。初期は食い違わない行を開く。回答が変わったときだけ同じ規則で更新し、手動の開閉はそれまで保つ。
  const [open, setOpen] = useState<Record<string, boolean>>(() => openState(c.answers, rules));
  const [outcome, setOutcome] = useState<ResultName | "">(c.result ?? "");
  // 保存済みの結果（開いたときの値。保存に成功したら更新する）と、今回の保存の状態。
  const [saved, setSaved] = useState<ResultName | "">(c.result ?? "");
  const [justSaved, setJustSaved] = useState<ResultName | "">("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  const rows = matchRules(answers, rules);
  const matchingCount = rows.filter((r) => !r.gray).length;
  const answeredCount = questions.filter((q) => answers[q.id] === "yes" || answers[q.id] === "no").length;
  const current = { ...c, answers };
  const counts = tally(current);
  const status = statusOf({ result: saved === "" ? undefined : saved, counts });

  const choose = (id: QuestionId, value: "yes" | "no") => {
    const changed = answers[id] !== value;
    const next = { ...answers, [id]: value };
    setAnswers(next);
    setEdited(new Set(edited).add(id));
    if (changed) setOpen(openState(next, rules));
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (outcome === "" || saving) return; // 二重送信しない
    setSaving(true);
    setSaveError("");
    try {
      // 保存済みの値から変わった設問だけを送る（Yes・Noに限る。確認待ちが変わらない設問は送らない）。
      const changed: HumanAnswers = {};
      for (const q of questions) {
        const value = answers[q.id];
        if ((value === "yes" || value === "no") && baseline[q.id] !== value) changed[q.id] = value;
      }
      await save(outcome, changed);
      setSaved(outcome);
      setBaseline(answers);
      setJustSaved(outcome);
    } catch (error) {
      // 失敗したときは、保存済みの表示を変えない。
      setJustSaved("");
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
        結果と、変更した回答は、［判定する］で保存されます。［判定する］を押さずに離れると、変更は保存されません（開き直すと、最後に保存した値に戻ります）。
      </p>

      <div className="columns">
        <section className="pane" aria-labelledby="q-title">
          <div className="pane-head">
            <h3 id="q-title">設問と回答</h3>
            <span role="status" data-testid="progress">{answeredCount}／{questions.length}問 入力済み</span>
          </div>
          <div className="pane-counts"><AnswerCounts counts={counts} /></div>
          <ol className="question-list">
            {questions.map((q) => {
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
                    <button type="button" aria-pressed={a === "yes"} onClick={() => choose(q.id, "yes")}>Yes</button>
                    <button type="button" aria-pressed={a === "no"} onClick={() => choose(q.id, "no")}>No</button>
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
        </section>

        <section className="pane" aria-labelledby="r-title">
          <div className="pane-head">
            <h3 id="r-title">照合結果</h3>
            <span role="status" data-testid="rule-count">照合 {matchingCount}／{rows.length}件</span>
          </div>
          <div className="rule-list">
            {rows.map(({ rule, gray, states }) => (
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
                  {gray && <span className="sr-only">（入力済みの回答と異なる条件があります）</span>}
                </summary>
                <dl className="conditions">
                  {questions.map((q) => (
                    <div key={q.id} className={states[q.id] === "match" ? "matched" : undefined} data-testid={`cond-${rule.id}-${q.id}`}>
                      <dt>{q.id}</dt>
                      <dd>{rule.expected[q.id] === "yes" ? "Yes" : "No"}{states[q.id] === "match" && <span className="sr-only">（入力済みの回答と一致）</span>}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            ))}
          </div>
        </section>
      </div>

      <form className="decision" aria-label="判定結果登録" onSubmit={submit}>
        <fieldset>
          <legend>判定結果</legend>
          <div className="outcomes">
            {RESULTS.map((r) => (
              <label key={r}>
                <input type="radio" name="outcome" value={r} required checked={outcome === r} onChange={() => setOutcome(r)} />
                {r}
              </label>
            ))}
          </div>
        </fieldset>
        <button className="btn action" type="submit" disabled={saving}>{saving ? "保存中…" : "判定する"}</button>
        {justSaved !== "" && <p className="saved" role="status" data-testid="registered">保存しました。登録結果: {justSaved}</p>}
        {saveError !== "" && <p className="error-inline" role="alert" data-testid="save-error">保存できませんでした。{saveError}。もう一度［判定する］を押してください。</p>}
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
