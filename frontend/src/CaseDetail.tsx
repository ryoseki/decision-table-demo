import { useState } from "react";
import { AnswerCounts } from "./AnswerCounts";
import { AnswerView, StatusBadge } from "./Badges";
import { cases, statusOf, tally, type Answer } from "./data/cases";
import { questions } from "./data/questions";

type Filter = "all" | "yes" | "no" | "pending" | "none";
const filters: readonly { key: Filter; label: string }[] = [
  { key: "all", label: "すべて" },
  { key: "yes", label: "Yes" },
  { key: "no", label: "No" },
  { key: "pending", label: "確認待ち" },
  { key: "none", label: "未回答" },
];

function matches(f: Filter, a: Answer | undefined): boolean {
  if (f === "all") return true;
  if (f === "none") return a === undefined;
  return a === f;
}

export default function CaseDetail({ caseId }: { caseId: string }) {
  const [filter, setFilter] = useState<Filter>("all");
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

  const t = tally(c);
  const answeredCount = t.yes + t.no;
  const visible = questions.filter((q) => matches(filter, c.answers[q.id]));

  return (
    <section aria-labelledby="detail-title">
      <a className="back" href="#/"><span aria-hidden="true">←</span> 一覧へ戻る</a>

      <div className="detail-head">
        <div>
          <h2 id="detail-title" tabIndex={-1}>{c.summary}<span className="sr-only">（{c.caseId}）</span></h2>
          <p className="sub-row">{c.caseId} ／ user_id {c.userId}</p>
        </div>
        <div className="detail-status">
          <StatusBadge status={statusOf(c)} />
          {c.result !== undefined && <span className="result">登録済みの結果: {c.result}</span>}
        </div>
      </div>

      <div className="inquiry">
        <h3>申告内容</h3>
        <p>{c.inquiry}</p>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h3 id="q-title">設問と回答</h3>
          <span className="panel-meta">
            {answeredCount}／{questions.length}問 入力済み
          </span>
        </div>

        <div className="panel-counts"><AnswerCounts c={c} /></div>
        <div className="chips panel-filter" role="group" aria-label="設問の表示を絞り込む">
          {filters.map((f) => (
            <button
              key={f.key}
              type="button"
              className="chip"
              aria-pressed={filter === f.key}
              onClick={() => setFilter(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>

        {visible.length === 0 ? (
          <div className="empty" data-testid="empty-questions">
            <p className="empty-title">この条件に当てはまる設問はありません</p>
            <button type="button" className="btn" onClick={() => setFilter("all")}>すべて表示</button>
          </div>
        ) : (
          <ol className="questions" aria-labelledby="q-title">
            {visible.map((q) => {
              const a = c.answers[q.id];
              return (
                <li key={q.id} className={`question q-${a ?? "none"}`}>
                  <div className="q-head">
                    <span className="q-id">{q.id}</span>
                    <p className="q-prompt">
                      {q.prompt}
                      {c.autoQuestions?.includes(q.id) === true && <span className="auto-tag"><span className="sr-only"> </span>自動</span>}
                    </p>
                    <AnswerView answer={a} />
                  </div>
                  {a === "pending" && (
                    <p className="warn">
                      {c.pendingReasons?.[q.id] ?? "資料を確認できていません。"}
                      Yes・Noのどちらにも数えません。
                    </p>
                  )}
                  <details className="material">
                    <summary>資料を見る</summary>
                    <dl>
                      <dt>資料と確認する欄</dt>
                      <dd>{q.source}</dd>
                      <dt>資料の記載（架空の記入例）</dt>
                      <dd>
                        {a === "yes" && q.yes}
                        {a === "no" && q.no}
                        {a === "pending" && <span className="muted">確認できていません。</span>}
                        {a === undefined && <span className="muted">まだ回答していません。</span>}
                      </dd>
                    </dl>
                  </details>
                </li>
              );
            })}
          </ol>
        )}
      </div>

      <p className="note">
        この画面は表示のみです（画面案は未承認）。回答の入力・保存、判定ルールとの照合、結果の登録は行いません。「自動」は表示用の架空データの印で、自動回答の対象設問を確定するものではありません。
      </p>
    </section>
  );
}
