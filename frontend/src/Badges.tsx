import type { CaseStatus } from "./data/cases";

const statusClass: Record<CaseStatus, string> = {
  未審査: "st-todo",
  取得エラー: "st-pending",
  登録済み: "st-done",
};

export function StatusBadge({ status }: { status: CaseStatus }) {
  return <span className={`badge ${statusClass[status]}`}>{status}</span>;
}
