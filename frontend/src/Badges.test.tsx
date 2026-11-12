// 実行: npm test（Vitest + jsdom）。画面の部品を描画して、見える内容を確認する。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { StatusBadge } from "./Badges";

// globalsを使わないため、描画の後始末は自分で登録する。
afterEach(cleanup);

test("状態バッジ: 状態名を表示し、状態ごとのクラスを付ける", () => {
  const cases = [
    ["未審査", "st-todo"],
    ["取得エラー", "st-pending"],
    ["登録済み", "st-done"],
  ] as const;
  for (const [status, className] of cases) {
    const { unmount } = render(<StatusBadge status={status} />);
    expect(screen.getByText(status).className).toBe(`badge ${className}`);
    unmount();
  }
});
