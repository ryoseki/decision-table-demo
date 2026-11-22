# Semgrep（静的解析）

PythonとTypeScriptのソースを、Semgrepの標準ルール（レジストリの `p/default`）で検査する。独立した新しいClaude Codeセッションのレビューが実行し、検出をコードと照合する。レビューの手順は [AGENTS.md](../AGENTS.md)、静的検査の全体は [testing-policy.md](testing-policy.md) を参照する。

## 実行

WSLのシェルで、リポジトリのルートから実行する。Docker Composeが必要。

```bash
docker compose run --rm semgrep
```

- 定義は `compose.yaml` の `semgrep` サービス（profile `tools`）。`docker compose up` の対象外で、上のコマンドで明示したときだけ動く。
- イメージは Semgrep 1.184.0 をdigestで固定して `compose.yaml` で参照する。更新するときは、公式のリリース情報とDocker Hubのdigestを確かめて直す。
- ソースは読み取り専用でマウントする。コンテナも読み取り専用（`/tmp` はtmpfs）、非root（1000:1000）、全capability削除、`no-new-privileges`。メトリクスは送らない（`--metrics off`）。UIDが異なる環境でソースを読めないときは、検査が完了しない。
- `p/default` は実行のたびにレジストリから取得する。ネットワークが必要で、ルールは固定しない。同じコミットでも、後日、検出が増減しうる。
- `--config auto` は、プロジェクトのURLを送るため使わない。

## 対象と除外

- 対象: `backend`、`db`、`e2e`、`frontend`、`infra`、`scripts`、`tests` の `*.py`・`*.ts`・`*.tsx`。
- 明示除外: `node_modules`、`__pycache__`、`.venv`、`dist`、`build`。
- `--no-git-ignore` を付け、`.gitignore` では除外しない。ただしSemgrepの既定の `.semgrepignore` は効き、テストのコードの一部が除外されうる。除外されたファイルは `--verbose` で確認できる。
- 対象外: `docs/`、`.github/`、`.tools/`、`.symphony/`、他の拡張子、Docker・YAML・シェルの設定。
- 作業コピー（ワークツリー）では、コンテナ内のGitが `.git` を読めず警告が出ることがある。警告があっても完了は保証されないため、スキャンの概要で確かめる。

## 終了コードと出力の確認

終了コードは、Docker ComposeまたはSemgrepのまま返る。

| コード | 意味 | 扱い |
| --- | --- | --- |
| 0 | 検出なしで完了した。 | 下の出力を確認してから報告する。 |
| 1 | 検出あり（`--error`）**または**Docker／Composeの失敗。コードでは区別できない。 | スキャンの概要があるときだけ検出ありとし、内容を読む。なければ未実行・失敗。 |
| その他 | Semgrepの失敗（ルール取得失敗、`--strict` を含む）やComposeのエラー。 | 完了していない。エラーを読む。 |

**終了コードだけで「問題なし」と報告しない。** 次を確認する。

- `Scanning N files` の言語ごとの件数に、PythonとTypeScriptが含まれるか。
- `Scan skipped` の内訳が想定の範囲か。
- エラー、`Parsed lines` が100%でない、「対象なし」の警告がないか。
- 検出は、該当の行を読んで、対応が要る・既存・文脈しだい・誤検出に分ける。

エラーが出た実行や、完了したスキャンの概要がない実行は、未実行・失敗として報告する。検出ゼロとは扱わない。

## 他の検査との関係

- 秘密情報の検査 `bash scripts/scan-secrets.sh`（Betterleaks）は、Semgrepで置き換えず実行する。
- `bash scripts/review-tests.sh` のテスト不足のレビューには、Semgrepの実行結果と検査の作業メモを渡さない（[testing-policy.md](testing-policy.md)）。

## 限界

- 標準ルールは、このアプリに合わせたものではない。「検出なし」は安全を意味しない。
- Semgrep OSS版はファイル単位の解析で、ファイルをまたぐデータの流れは追わない。
- 一部のテストコードが除外されうる（上記）。

## 参考

- [Semgrep CLI reference](https://docs.semgrep.dev/cli-reference)
- [Running rules](https://docs.semgrep.dev/running-rules)
