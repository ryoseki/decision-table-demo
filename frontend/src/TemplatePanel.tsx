import { useEffect, useState } from "react";
import { describeError } from "./api";
import { call as callApi } from "./questionsApi";

// 設問・ルールの画面で共有する。エラーの文言に、設問一覧と書かない。
const SUBJECT = "The list of saved requests";
const NETWORK_UNKNOWN = "Could not connect to the server. It is unknown whether the request was deleted. Reload the page to check.";
const call = (path: string, init?: RequestInit) => callApi(path, init, NETWORK_UNKNOWN, SUBJECT, "");

export const TEMPLATE_HEADING = "Saved requests (exact match, no AI call)";
export const TEMPLATE_NOTE = "If an unintended change proposal appears, delete the matching request.";

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
            <button type="button" className="btn ghost" disabled={busy} aria-label={`Delete ${prompt} from saved requests`}
              onClick={() => void remove(prompt)}>Delete</button>
          </li>
        ))}
      </ul>
    </details>
  );
}
