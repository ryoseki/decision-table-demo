// 実行: npm test（Vitest + jsdom）。生成元（モックか実接続か）の表示部品を、描画して確認する。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { AiNote, SourceBadge } from "./SourceBadge";

afterEach(cleanup);

test("モック: 「生成元: モック」と、実際のAIではないことを文字で示す", () => {
  render(<SourceBadge source={{ kind: "mock" }} />);
  const badge = screen.getByTestId("source-badge");
  expect(badge.dataset.source).toBe("mock");
  expect(badge.textContent).toContain("生成元: モック");
  expect(badge.textContent).toContain("実際のAIではありません");
  expect(badge.textContent).not.toContain("Bedrock");
});

test("実接続: 「生成元: Amazon Bedrock（実接続）」と、モデル・リージョンを示す", () => {
  render(<SourceBadge source={{ kind: "bedrock", modelId: "demo.model-v1:0", region: "region-demo-1" }} />);
  const badge = screen.getByTestId("source-badge");
  expect(badge.dataset.source).toBe("bedrock");
  expect(badge.textContent).toContain("生成元: Amazon Bedrock（実接続）");
  expect(badge.textContent).toContain("モデル: demo.model-v1:0");
  expect(badge.textContent).toContain("リージョン: region-demo-1");
  expect(badge.textContent).not.toContain("モックです");
});

test("説明文: モックは接続していないと書き、実接続は料金が発生することと、検証・承認前は反映しないことを書く", () => {
  const { container, rerender } = render(<AiNote source={{ kind: "mock" }} />);
  expect(container.textContent).toContain("モックです");
  expect(container.textContent).toContain("実際のAIには接続していません");
  expect(container.textContent).not.toContain("料金");

  rerender(<AiNote source={{ kind: "bedrock", modelId: "demo.model-v1:0", region: "region-demo-1" }} />);
  expect(container.textContent).toContain("実接続です（Amazon Bedrock）");
  expect(container.textContent).toContain("demo.model-v1:0");
  expect(container.textContent).toContain("利用料金が発生します");
  expect(container.textContent).toContain("Yesを選ぶまで反映しません");
  expect(container.textContent).not.toContain("モックです");
  expect(container.textContent).not.toContain("実際のAIには接続していません");

  rerender(<AiNote source={null} />);
  expect(container.textContent).toContain("確認しています");
  expect(container.textContent).not.toContain("モックです");

  // 一覧の取得に失敗したときは、読み込み中のままのように見せない（どちらとも言わない）。
  rerender(<AiNote source={null} failed />);
  expect(container.textContent).toContain("確認できませんでした");
  expect(container.textContent).not.toContain("確認しています");
  expect(container.textContent).not.toContain("モックです");
  expect(container.textContent).not.toContain("実接続です");
});
