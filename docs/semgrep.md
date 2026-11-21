# Semgrep（静的解析）

PythonとTypeScriptのソースを、Semgrepの標準ルール（レジストリの `p/default`）で検査する。独立した新しいClaude Codeセッションのレビューが実行し、検出をコードと照合する。要点は [AGENTS.md](../AGENTS.md)、静的検査の全体は [testing-policy.md](testing-policy.md) にある。

標準のルールとCLIをそのまま使うのは、プロジェクト独自の判断である（OpenAIが定める仕様ではない）。Semgrep本体と標準ルールの検出能力は、このプロジェクトでは検証しない。

## 使い方

WSLのシェルで、リポジトリのルートから実行する。`docker` が必要。

```bash
bash scripts/semgrep.sh
```

初回は、固定したイメージ（Semgrep 1.184.0、digest固定）を取得する。ソースは読み取り専用・非root・Linuxの権限なしのコンテナで検査する。メトリクスは送らない（`--metrics off`）。`--config auto` は、プロジェクトのURLをSemgrepへ送るため、使わない。

### 終了コード

| コード | 意味 | 扱い |
| --- | --- | --- |
| 0 | Semgrepが、検出なしで完了した。 | 下の「出力の確認」をしてから、報告する。 |
| 1 | 検出あり（`--error`）。 | 内容を読んで判断する。 |
| 125 | Dockerに接続できない、またはコンテナを起動できない。 | **未実行**。問題なしとは扱わない。 |
| 127 | `docker` がない。 | **未実行**。 |
| 2 | 実行の失敗。ラッパーへ引数を渡したときの使い方の誤り（引数は受け付けない）と、Semgrep自身の汎用の実行失敗（ルールの取得失敗など）が、同じ2になる。 | 検査を完了していない。出力のエラーを読む。クリーンとは扱わない。 |
| その他 | Semgrepの失敗（`--strict` を含む）。 | 検査を完了していない。 |

## 対象と除外

- 対象: `backend`、`db`、`e2e`、`frontend`、`infra`、`scripts`、`tests` の、`*.py`・`*.ts`・`*.tsx`。
- 除外: `node_modules`、`__pycache__`、`.venv`、`dist`、`build`。
- `--no-git-ignore` を付け、`.gitignore` では除外しない。ただし、Semgrepの既定の `.semgrepignore` は効く。**この除外には、テストのディレクトリ（`tests/` など）が含まれる見込みで、実際に1回目の実行で21ファイルがこの除外にあたった**（一覧は `--verbose` で確認できる。今回は列挙していない）。テストのコードは、検査されていないものがあると考える。
- 対象外: `docs/`、`.github/`、`.tools/`、`.symphony/`、`.js` などの他の拡張子、Docker・YAML・シェルの設定。
- ラッパーはGitを呼び出さない。ただしSemgrep自身が、メタデータの取得のためにGitを使うことがある。作業コピー（ワークツリー）では、コンテナの中のGitが `.git` を読めず、警告が出ることがある。警告があっても、検査の続行や完了は保証されない。実際のスキャンの概要（検査したファイル数、エラー）で確かめる。`safe.directory` の緩和などはしない。

## 出力の確認

**終了コードだけで「問題なし」と報告しない。** 標準の出力の概要を読む。

- `Scanning N files`（言語ごとの件数）に、PythonとTypeScriptが含まれるか。
- `Scan skipped` の内訳（`--include` に合わない、`--exclude`、`.semgrepignore`）が、想定の範囲か。
- エラー、`Parsed lines` が100%でない、「対象なし」の警告がないか。あれば、検査は完了していない。
- 検出は、該当の行を読んで、真の問題・誤検出・文脈しだいのものを判断する。既存のコードの検出と、差分に関わる検出を分ける。

## ルールと更新

- `p/default` は、Semgrepのレジストリの標準ルールで、**実行のたびに取得する**。ネットワークが必要で、コンテナのネットワークは有効（Dockerの既定）。通信先の制限（ファイアウォールなど）は、していない。
- ルールは、Semgrep側の更新で変わる。同じコミットでも、後日、検出が増減することがある。同じ結果を再現することは、約束しない。ルールを固定する仕組みは持たない。
- Semgrepの版は、`scripts/semgrep.sh` の `SEMGREP_IMAGE` のdigestで固定している。更新するときは、公式のリリース情報と、Docker Hubのdigestを確かめて、直す。

## 独立したレビューでの使い方

ファイル作業の後、同じ作業コピーで起動する新しいClaude Codeセッションが、変更対象と受入条件を受け取り、次を行う。

1. 差分を照合する。
2. `bash scripts/semgrep.sh` を実行し、終了コードと、出力の概要（上の「出力の確認」）を報告する。125・127・その他の失敗は、「未実行」「失敗」と報告する。
3. 検出を読んで、対応が要る・既存・文脈しだい・誤検出・後続課題に分ける。アプリの仕様変更や既存検査の緩和が必要なときは、人に相談する。
4. 秘密情報の検査 `bash scripts/scan-secrets.sh`（Betterleaks）は、これまでどおり実行する。Semgrepで置き換えない。
5. 文書だけの変更では、`git diff --check` と内容の整合性を確認する。

`claude -p` で起動するときは、書き込みと `.env` の読み取りを許さず、必要なコマンドだけを完全一致で許す（`git diff --check`・`git diff origin/main`・`git status --short`・`bash scripts/semgrep.sh`・`bash scripts/scan-secrets.sh`）。指摘への対応が必要なときは、`Edit`・`Write` を許す別の起動にする。

### テスト不足のレビューには渡さない

`bash scripts/review-tests.sh` のテスト不足のレビューは、別の独立した新しいセッションで行う。**Semgrepの実行結果と、検査の作業メモは渡さない**（検証結果と作業メモを渡さない、既存の決まりのとおり。[testing-policy.md](testing-policy.md)）。`review-tests.sh` は変えていない。

## 限界

- 標準ルールの検出範囲は、このプロジェクトのアプリに合わせたものではない。「検出なし」は、アプリが安全であることを意味しない。
- Semgrepは、ファイル単位の解析（OSS版）で、ファイルをまたぐデータの流れは追わない。
- テストのディレクトリが除外される（上記）。
- PRごとのCI、週次実行、マージを止める条件は、実行時間と指摘を見て、別途判断する。

## 参照した公式資料

2026-11-22（日本時間）に、Codexが本文を確認した。

- [Semgrep CLI reference](https://docs.semgrep.dev/cli-reference): `semgrep scan` の `--config`、`--error`、`--strict`、`--metrics`、終了コード。
- [Running rules](https://docs.semgrep.dev/running-rules): レジストリのルールセット（`p/default`）の指定。
- [Custom code review rules for Codex（OpenAI）](https://developers.openai.com/blog/custom-code-review-rules-for-codex): リポジトリのレビュー規約を、明示して書く。
