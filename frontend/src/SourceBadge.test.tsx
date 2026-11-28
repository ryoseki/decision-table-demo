// 実行: npm test（Vitest + jsdom）。生成元（モックか実接続か）の表示部品を、描画して確認する。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { AiNote, SourceBadge } from "./SourceBadge";

afterEach(cleanup);

test("モック: 「生成元: モック」と、実際のAIではないことを文字で示す", () => {
  render(<SourceBadge source={{ kind: "mock" }} />);
  const badge = screen.getByTestId("source-badge");
  expect(badge.dataset.source).toBe("mock");
  expect(badge.textContent).toContain("Source: Mock");
  expect(badge.textContent).toContain("not a real AI");
  expect(badge.textContent).not.toContain("Bedrock");
});

test("実接続: 「生成元: Amazon Bedrock（実接続）」と、モデル・リージョンを示す", () => {
  render(<SourceBadge source={{ kind: "bedrock", modelId: "demo.model-v1:0", region: "region-demo-1" }} />);
  const badge = screen.getByTestId("source-badge");
  expect(badge.dataset.source).toBe("bedrock");
  expect(badge.textContent).toContain("Source: Amazon Bedrock (live connection)");
  expect(badge.textContent).toContain("Model: demo.model-v1:0");
  expect(badge.textContent).toContain("Region: region-demo-1");
  expect(badge.textContent).not.toContain("This is a mock");
});

test("説明文: モックは接続していないと書き、実接続は料金が発生することと、検証・承認前は反映しないことを書く", () => {
  const { container, rerender } = render(<AiNote source={{ kind: "mock" }} />);
  expect(container.textContent).toContain("This is a mock");
  expect(container.textContent).toContain("no real AI is connected");
  expect(container.textContent).not.toContain("charges");

  rerender(<AiNote source={{ kind: "bedrock", modelId: "demo.model-v1:0", region: "region-demo-1" }} />);
  expect(container.textContent).toContain("Live connection (Amazon Bedrock)");
  expect(container.textContent).toContain("demo.model-v1:0");
  expect(container.textContent).toContain("incurs usage charges");
  expect(container.textContent).toContain("until you choose Yes");
  expect(container.textContent).not.toContain("This is a mock");
  expect(container.textContent).not.toContain("no real AI is connected");

  rerender(<AiNote source={null} />);
  expect(container.textContent).toContain("Checking the AI source");
  expect(container.textContent).not.toContain("This is a mock");

  // 一覧の取得に失敗したときは、読み込み中のままのように見せない（どちらとも言わない）。
  rerender(<AiNote source={null} failed />);
  expect(container.textContent).toContain("Could not determine the AI source");
  expect(container.textContent).not.toContain("Checking the AI source");
  expect(container.textContent).not.toContain("This is a mock");
  expect(container.textContent).not.toContain("Live connection");
});
