// 実行: npm test（Vitest）。サーバーの source（生成元）の読み取りを確認する。
import { expect, test } from "vitest";
import { toSource } from "./generationSource";

test("bedrock と明示され、モデルとリージョンがあれば、実接続として読む", () => {
  expect(toSource({ kind: "bedrock", model_id: "demo.model-v1:0", region: "region-demo-1" })).toEqual({
    kind: "bedrock",
    modelId: "demo.model-v1:0",
    region: "region-demo-1",
  });
});

test("bedrock でモデルやリージョンが欠けても、実接続のまま（モックに見せない）。値は「不明」", () => {
  expect(toSource({ kind: "bedrock" })).toEqual({ kind: "bedrock", modelId: "unknown", region: "unknown" });
  expect(toSource({ kind: "bedrock", model_id: "", region: 3 })).toEqual({ kind: "bedrock", modelId: "unknown", region: "unknown" });
});

test("mock、欠落、想定外の形は、モックとして読む", () => {
  for (const raw of [{ kind: "mock" }, undefined, null, "bedrock", 1, {}, { kind: "other" }]) {
    expect(toSource(raw), JSON.stringify(raw)).toEqual({ kind: "mock" });
  }
});
