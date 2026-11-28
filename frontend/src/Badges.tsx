import type { CaseStatus } from "./data/cases";

const statusClass: Record<CaseStatus, string> = {
  "Not reviewed": "st-todo",
  "Pending data": "st-pending",
  Registered: "st-done",
};

export function StatusBadge({ status }: { status: CaseStatus }) {
  return <span className={`badge ${statusClass[status]}`}>{status}</span>;
}
