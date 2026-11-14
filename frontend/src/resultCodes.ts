// 結果の表示名と、APIの結果コードの対応。コードは、保存するときの値（2026-11-12に確定）。
// 対応は、ここの1か所だけに置く（backend/app/domain.py の RESULT_CODES と同じ4つ）。
import type { ResultName } from "./data/cases";

export const RESULT_CODE_BY_NAME: Record<ResultName, string> = {
  アカウント削除: "account_deletion",
  アカウント一時凍結: "account_suspension",
  注意: "caution",
  問題なし: "no_issue",
};

const NAME_BY_CODE = new Map<string, ResultName>(
  (Object.entries(RESULT_CODE_BY_NAME) as [ResultName, string][]).map(([name, code]) => [code, name]),
);

/** 結果コードから表示名を返す。定義にないコードは、例外にする（黙って別の結果に見せない）。 */
export function resultNameOf(code: string): ResultName {
  const name = NAME_BY_CODE.get(code);
  if (name === undefined) throw new Error(`定義にない結果コードです: ${code}`);
  return name;
}
