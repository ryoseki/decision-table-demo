import { useState, type FormEvent } from "react";
import { AnswerCounts } from "./AnswerCounts";
import { StatusBadge } from "./Badges";
import { cases, statusOf, type Answer, type ResultName } from "./data/cases";
import { questions, type QuestionId } from "./data/questions";
import { RESULTS, canSkipQuestion, matchRules, type AnswerMap } from "./matching";

// 状態はこのコンポーネントのメモリ上だけに持つ。保存・永続化・通信は行わない（契約は未合意）。
// ページを開き直すと初期状態に戻る。

function openState(answers: AnswerMap): Record<string, boolean> {
  return Object.fromEntries(matchRules(answers).map((r) => [r.rule.id, !r.gray]));
}

export default function CaseDetail({ caseId }: { caseId: string }) {
  const c = cases.find((x) => x.caseId === caseId);

  if (c === undefined) {
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
  return <Review c={c} />;
}

function Review({ c }: { c: (typeof cases)[number] }) {
  const [answers, setAnswers] = useState<AnswerMap>(c.answers);
  const [edited, setEdited] = useState<ReadonlySet<QuestionId>>(new Set());
  // 行の開閉。初期は食い違わない行を開く。回答が変わったときだけ同じ規則で更新し、手動の開閉はそれまで保つ。
  const [open, setOpen] = useState<Record<string, boolean>>(() => openState(c.answers));
  const [outcome, setOutcome] = useState<ResultName | "">(c.result ?? "");
  const [registered, setRegistered] = useState<ResultName | "">("");

  const rows = matchRules(answers);
  const matchingCount = rows.filter((r) => !r.gray).length;
  const answeredCount = questions.filter((q) => answers[q.id] === "yes" || answers[q.id] === "no").length;
  const current = { ...c, answers };

  const choose = (id: QuestionId, value: "yes" | "no") => {
    const changed = answers[id] !== value;
    const next = { ...answers, [id]: value };
    setAnswers(next);
    setEdited(new Set(edited).add(id));
    if (changed) setOpen(openState(next));
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (outcome !== "") setRegistered(outcome);
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
          <StatusBadge status={statusOf(current)} />
          {c.result !== undefined && <span className="result">登録済みの結果: {c.result}</span>}
        </div>
      </div>

      <div className="inquiry">
        <h3>申告内容</h3>
        <p>{c.inquiry}</p>
      </div>

      <p className="note note-top" data-testid="preview-note">
        画面案：保存はまだ行いません。回答と結果の選択は、このページを開いている間だけの表示用で、開き直すと初期状態に戻ります。
      </p>

      <div className="columns">
        <section className="pane" aria-labelledby="q-title">
          <div className="pane-head">
            <h3 id="q-title">設問と回答</h3>
            <span role="status" data-testid="progress">{answeredCount}／{questions.length}問 入力済み</span>
          </div>
          <div className="pane-counts"><AnswerCounts c={current} /></div>
          <ol className="question-list">
            {questions.map((q) => {
              const a: Answer | undefined = answers[q.id];
              const optional = a === undefined && canSkipQuestion(q.id, answers);
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
        <button className="btn action" type="submit">判定する</button>
        {registered !== "" && <p className="saved" role="status" data-testid="registered">登録結果（画面例） {registered}</p>}
        <p className="note note-inline">
          結果は照合にかかわらず、未回答・確認待ちが残っていても選べます。［判定する］は画面に結果例を出すだけで、保存・実際の処置は行いません（未実装）。
        </p>
      </form>

      <p className="note">
        「自動」は表示用の架空データの印で、自動回答の対象設問を確定するものではありません。R01〜R03は受入例用の架空の組み合わせで、業務上の認定ルールではありません。
      </p>
    </section>
  );
}
