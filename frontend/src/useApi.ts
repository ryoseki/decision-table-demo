import { useEffect, useRef, useState } from "react";
import { createSampleCase, describeError, getCase, listCases, listRules, proposalRequestId, type CaseListRow } from "./api";
import type { Rule } from "./matching";
import type { ReviewCase } from "./data/cases";
import { getDemoQuestions, type DemoQuestionList } from "./questionsApi";
import { getDemoRules, type DemoRuleList } from "./rulesApi";

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

/** サンプル案件の作成の状態と操作（useSampleCreation の戻り値）。 */
export type SampleCreation = {
  /** 作成APIが成功するたびに進む。一覧の取り直しの合図。 */
  version: number;
  creating: boolean;
  notice: string | null;
  /** 通知に表示する案件ID。 */
  noticeCaseId: string | null;
  error: string | null;
  create: () => void;
};

/**
 * 通信失敗時に同じ操作IDで再試行できるよう、Appの生存中は未完了の操作を保持する。
 * 一覧を離れて戻っても引き継ぎ、成功後の次の作成には新しいIDを使う。
 * ブラウザー全体の再読み込みやタブを閉じたあとは引き継がない。
 */
export function useSampleCreation(): SampleCreation {
  const [version, setVersion] = useState(0);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeCaseId, setNoticeCaseId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const operationId = useRef<string | null>(null);
  const create = async () => {
    operationId.current ??= proposalRequestId();
    setCreating(true);
    setNotice(null);
    setNoticeCaseId(null);
    setError(null);
    try {
      const result = await createSampleCase(operationId.current);
      operationId.current = null; // 次の押下は、新しい操作
      setNotice(
        result.created
          ? `サンプル案件を1件作成しました（${result.caseId}）。`
          : `この操作の案件（${result.caseId}）は作成済みでした。新しく作成した案件はありません。`,
      );
      setNoticeCaseId(result.caseId);
      setVersion((v) => v + 1);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setCreating(false);
    }
  };
  return { version, creating, notice, noticeCaseId, error, create };
}

/** 案件の一覧。version を変えると、取り直す（サンプル案件の作成後など）。 */
export function useCaseList(version = 0): Remote<CaseListRow[]> {
  return useRemote(listCases, `list:${version}`);
}

/** デモの設問一覧（設問管理）。version を変えると、取り直す。 */
export function useDemoQuestions(version: number): Remote<DemoQuestionList> {
  return useRemote(getDemoQuestions, `demo-questions:${version}`);
}

/** デモの判定ルール一覧（判定ルール管理）。version を変えると、取り直す。 */
export function useDemoRules(version: number): Remote<DemoRuleList> {
  return useRemote(getDemoRules, `demo-rules:${version}`);
}

export function useCase(caseId: string): Remote<ReviewCase> {
  return useRemote(() => getCase(caseId), `case:${caseId}`);
}

export function useRules(): Remote<Rule[]> {
  return useRemote(listRules, "rules");
}
