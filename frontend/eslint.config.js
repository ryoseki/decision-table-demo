import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: reactHooks.configs.recommended.rules,
  },
  // 依存の向きの検査。ロジック（src 直下の .ts とデータ）は、React を取り込まない。
  // React を使うもの（画面の部品 PascalCase の .tsx、フック use〇〇.ts）は、検査の対象外。
  // 違反したとき、message がそのまま表示される。層の説明は ARCHITECTURE.md。
  {
    files: ["src/**/*.ts"],
    // テスト、React を使うフック（use〇〇.ts）、型定義（.d.ts）は対象外。
    ignores: ["src/**/*.test.ts", "src/**/use*.ts", "src/**/*.d.ts"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["react", "react-dom", "react/*", "react-dom/*"],
              message:
                "ロジック（src 直下の .ts・data/）は React に依存しない。直し方: 画面の都合（状態・描画）は部品（.tsx）かフック（use〇〇.ts）に置き、ロジックは純粋な関数・データとして切り出す。詳細は ARCHITECTURE.md。",
            },
          ],
        },
      ],
    },
  },
);
