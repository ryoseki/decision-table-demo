import { useEffect, useState } from "react";
import { describeError } from "./api";
import { call as callApi } from "./questionsApi";

// 設問・ルールの画面で共有する。エラーの文言に、設問一覧と書かない。
const SUBJECT = "AIを経由せずに反映する変更の依頼の一覧";
const NETWORK_UNKNOWN = "サーバーに接続できませんでした。削除されたかどうかを確認できていません。画面を再読み込みして確認してください。";
const call = (path: string, init?: RequestInit) => callApi(path, init, NETWORK_UNKNOWN, SUBJECT, "提案");

export const TEMPLATE_HEADING = "AIを経由せずに反映する変更の依頼(完全一致)";
export const TEMPLATE_NOTE = "意図と異なる変更案が出る場合は、該当する依頼を削除してください。";

/**
 * 定型文（完全一致の依頼）の一覧。登録は、Yesで反映したときにサーバーが自動で行うため、この部品には
 * 登録の操作がない。削除だけできる。refreshKey が変わると（Yesで反映したあと）、一覧を取得し直す。
 */
export function TemplatePanel({ target, refreshKey }: { target: "question" | "rule"; refreshKey: number }) {
  const path = `/api/admin/proposal-templates/${target}`;
  const [prompts, setPrompts] = useState<string[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void call(path).then(
      (body) => { if (active) { setPrompts((body as { prompts: string[] }).prompts); setError(""); } },
      (cause: unknown) => { if (active) setError(describeError(cause)); },
    ).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [path, refreshKey]);

  async function remove(prompt: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await call(path + "/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }),
      }) as { prompts: string[] };
      setPrompts(result.prompts);
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="template-panel" data-testid="template-panel">
      <summary>{TEMPLATE_HEADING}</summary>
      <p><small>{TEMPLATE_NOTE}</small></p>
      {error !== "" && <p role="alert">{error}</p>}
      <ul>
        {prompts.map((prompt) => (
          <li key={prompt}>
            {prompt}{" "}
            <button type="button" className="btn ghost" disabled={busy} aria-label={`${prompt}を定型文から削除`}
              onClick={() => void remove(prompt)}>削除</button>
          </li>
        ))}
      </ul>
    </details>
  );
}
