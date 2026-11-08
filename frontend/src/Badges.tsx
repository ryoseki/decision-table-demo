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

// 色だけに頼らず、記号と文言で Yes / No / 確認待ち / 未回答 を区別する。
export function AnswerBadge({ answer }: { answer: Answer | undefined }) {
  switch (answer) {
    case "yes":
      return <span className="badge ans-yes"><span aria-hidden="true">●</span> Yes</span>;
    case "no":
      return <span className="badge ans-no"><span aria-hidden="true">○</span> No</span>;
    case "pending":
      return <span className="badge ans-pending"><span aria-hidden="true">▲</span> 確認待ち</span>;
    default:
      return <span className="badge ans-none"><span aria-hidden="true">―</span> 未回答</span>;
  }
}
