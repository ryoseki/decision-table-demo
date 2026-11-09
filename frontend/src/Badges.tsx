import type { Answer, CaseStatus } from "./data/cases";

const statusClass: Record<CaseStatus, string> = {
  未着手: "st-todo",
  回答中: "st-doing",
  確認待ちあり: "st-pending",
  登録済み: "st-done",
};

export function StatusBadge({ status }: { status: CaseStatus }) {
  return <span className={`badge ${statusClass[status]}`}>{status}</span>;
}

// 読み取り専用の Yes / No 表示。ボタンに見えないよう枠を持たせず、選択側だけを ✓ 付きで塗る。
// 確認待ち・未回答は、どちらも塗らず、記号と文言の札を添えて区別する。
export function AnswerView({ answer }: { answer: Answer | undefined }) {
  const label =
    answer === "yes" ? "Yes" : answer === "no" ? "No" : answer === "pending" ? "確認待ち" : "未回答";
  return (
    <div className="answer-view">
      <span className="sr-only">回答: {label}</span>
      <div className="choices" aria-hidden="true">
        <span className={`choice${answer === "yes" ? " on" : ""}`}>{answer === "yes" ? "✓ " : ""}Yes</span>
        <span className={`choice${answer === "no" ? " on" : ""}`}>{answer === "no" ? "✓ " : ""}No</span>
      </div>
      {answer === "pending" && <span className="badge ans-pending" aria-hidden="true">▲ 確認待ち</span>}
      {answer === undefined && <span className="badge ans-none" aria-hidden="true">― 未回答</span>}
    </div>
  );
}
