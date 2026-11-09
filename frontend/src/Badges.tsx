import type { CaseStatus } from "./data/cases";

const statusClass: Record<CaseStatus, string> = {
  未着手: "st-todo",
  回答中: "st-doing",
  確認待ちあり: "st-pending",
  登録済み: "st-done",
};

export function StatusBadge({ status }: { status: CaseStatus }) {
  return <span className={`badge ${statusClass[status]}`}>{status}</span>;
}
