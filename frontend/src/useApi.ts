import { useEffect, useState } from "react";
import { getCase, listCases, type CaseListRow } from "./api";
import type { ReviewCase } from "./data/cases";
import { getDemoQuestions, type DemoQuestionList } from "./questionsApi";

// 画面の部品から使うフック（Reactに依存する）。読み込み中・成功・失敗を返す。
export type Remote<T> =
  | { status: "loading" }
  | { status: "ok"; data: T }
  | { status: "error"; error: unknown };

function useRemote<T>(load: () => Promise<T>, key: string): Remote<T> {
  const [state, setState] = useState<{ key: string; value: Remote<T> }>({ key, value: { status: "loading" } });
  useEffect(() => {
    let cancelled = false;
    load().then(
      (data) => !cancelled && setState({ key, value: { status: "ok", data } }),
      (error: unknown) => !cancelled && setState({ key, value: { status: "error", error } }),
    );
    return () => {
      cancelled = true;
    };
    // load は key から決まる。key が変わったときだけ読み込み直す。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  // key が変わった直後は、前の結果ではなく、読み込み中を返す。
  return state.key === key ? state.value : { status: "loading" };
}

export function useCaseList(): Remote<CaseListRow[]> {
  return useRemote(listCases, "list");
}

/** デモの設問一覧（設問管理）。version を変えると、取り直す。 */
export function useDemoQuestions(version: number): Remote<DemoQuestionList> {
  return useRemote(getDemoQuestions, `demo-questions:${version}`);
}

export function useCase(caseId: string): Remote<ReviewCase> {
  return useRemote(() => getCase(caseId), `case:${caseId}`);
}
