// 提案の生成元（モックか、Amazon Bedrock の実接続か）。Reactに依存しない。
// サーバーの応答の source を読む。画面と検証記録で、どちらで動いたかを区別するための値。

export type GenerationSource = { kind: "mock" } | { kind: "bedrock"; modelId: string; region: string };

/**
 * サーバーの source を読む。bedrock と明示されたときだけ実接続として扱い、それ以外（欠落を含む）は
 * モックとして扱う。サーバーは、モックでも必ず source を返す。
 */
export function toSource(raw: unknown): GenerationSource {
  if (typeof raw === "object" && raw !== null && (raw as { kind?: unknown }).kind === "bedrock") {
    const { model_id: modelId, region } = raw as { model_id?: unknown; region?: unknown };
    return {
      kind: "bedrock",
      modelId: typeof modelId === "string" && modelId !== "" ? modelId : "不明",
      region: typeof region === "string" && region !== "" ? region : "不明",
    };
  }
  return { kind: "mock" };
}
