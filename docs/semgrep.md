# Semgrep（静的解析）

PythonとTypeScriptのソースを、Semgrepで検査する。独立した新しいClaude Codeセッションのレビューが、実行して、結果を確認する。要点は [AGENTS.md](../AGENTS.md)、層ごとのテストと静的検査の全体は [docs/testing-policy.md](testing-policy.md) にある。ここには、版、ルール、対象、結果の読み方、レビューでの使い方を書く。

この導入は、プロジェクト独自の判断である。次の3点は、OpenAIが必須とする仕様ではない。

- ルールとSemgrepの版を、リポジトリで固定する。
- 検査を、Claude Codeの独立したレビューで確認する。
- 実行を、WSL（Linux）の `docker` に限る。

参照した公式資料は、末尾にまとめた。

## 使い方

WSLのシェルで、リポジトリのルートから実行する。`docker` と `python3` が必要。

```bash
bash scripts/semgrep.sh              # ソースを検査する（scan と同じ）
bash scripts/semgrep.sh fixtures     # ルールを、架空の違反例で検査する（ルールを変えたときは必須）
bash scripts/semgrep.sh scan --root <ディレクトリ>   # 検査する場所を変える（ルールはこのリポジトリのもの）
python3 -B -I -m unittest discover -s tests/semgrep   # 入口・適用範囲・文書の整合・review-tests.sh の境界のテスト（約2分）
```

初回だけ、固定したイメージを `docker pull` で取得する。検査そのものは、ネットワークなし（`--network none`）・ソースは読み取り専用・非root・Linuxの権限なしのコンテナで動く。ルールは、ローカルのファイルだけを使う（Semgrepのレジストリは使わない）。メトリクスは送らない。

### 終了コード（結果の4区分）

| コード | 意味 | 扱い |
| --- | --- | --- |
| 0 | 問題なし。1件以上のファイルを検査し、検出なし。 | 出力の「N ファイルを検査」で、検査した数と拡張子の内訳を確かめる。 |
| 10 | 検出あり。 | 内容を読んで、真か誤検出かを判断する。 |
| 20 | 実行できない。`docker`・`python3` がない、Dockerデーモンに接続できない、固定イメージを入手できない。 | **未実行**として報告する。問題なしとは扱わない。 |
| 30 | 失敗。Semgrepの異常終了、設定・ルールの誤り、構文を解析できないファイル、検査されなかったファイル、**対象が0件**（何も検査していない）、版の不一致、フィクスチャの期待との不一致、Semgrepが検出ありの終了コード（1）を返したのに出力の検出が空という矛盾。 | 検査を完了していない。原因を直すまで、結果を信用しない。 |
| 2 | 引数の誤り。 | — |

Semgrepの終了コードだけでは判定しない。出力（JSON）の `errors`、検査したファイルの一覧、版を確かめ、渡した全ファイルが検査されたときだけ、0か10にする。

## 版の固定

- Semgrep **1.184.0**（公開 2026-11-05）。コンテナイメージ `semgrep/semgrep` を、**digest**（`sha256:cc1b68266f267878b39739373ea9985b9a78e5d64497dd875c8de100b22695ed`。複数のアーキテクチャをまとめたもの）で固定している。値は、`scripts/semgrep.sh` の `SEMGREP_VERSION` と `SEMGREP_IMAGE`。
- 実在の確認（2026-11-20）: GitHubの `semgrep/semgrep` のリリース情報（タグ `v1.184.0`）と、Docker Hubのタグ `1.184.0` のdigestを照合した。取得したイメージで `semgrep --version` が `1.184.0` を返すこと、実際の検査でも版が一致することを、出力の版の照合で確かめている。
- 最新ではなく1.184.0にした理由: 2026-11-20時点の最新は1.186.0（公開 2026-11-18）。このプロジェクトの慣例（依存は、公開から2週間以上経った版を、正確な版で固定する。[testing-policy](testing-policy.md)）に合わせ、2週間以上経った最新の版を選んだ。
- 更新するとき: 公式のリリース情報で、実在する版と、公開日を確認する。Docker Hubで、同じ版のタグのdigestを確認する。`SEMGREP_VERSION` と `SEMGREP_IMAGE` を一緒に直し、`fixtures`・`scan`・`tests/semgrep` を実行する。検出が増減したら、初回の確認記録（下の「検出の確認記録」）と見比べる。

## 対象と除外

対象は、`scripts/semgrep.sh` が、ファイルを列挙して、Semgrepへ明示的に渡す（`.gitignore` や既定の除外に任せない）。

| 項目 | 内容 |
| --- | --- |
| 対象のディレクトリ | `backend`、`db`、`e2e`、`frontend`、`infra`、`scripts`、`tests` |
| 対象の拡張子 | `.py`、`.ts`、`.tsx`、`.js`、`.jsx`、`.mjs`、`.cjs` |
| 除外するディレクトリ名 | `node_modules`、`__pycache__`、`.venv`、`venv`、`dist`、`build`、`coverage`、`playwright-report`、`test-results`、`.import_linter_cache`、`.ruff_cache`、`.mypy_cache` |
| 対象に含まれない | `security/`（ルールと、架空の違反例）、`docs/`、`.github/`、`.tools/`、`.symphony/`、Docker・YAML・JSON・シェルなどの設定とスクリプト |
| シンボリックリンク | たどらない（ルートの外を読まない）。ファイルのリンク、ディレクトリのリンク、対象のディレクトリそのものがリンクのとき、同じルートの中を指すリンクも、除外する。リンクごとの警告は出さない |

- テスト（`backend/tests`、`*.test.*`、`e2e/`）も対象に含める。Semgrepの既定は、テストのディレクトリを除外するため、ファイルを明示して渡している。
- 対象を足す・減らすときは、`scripts/semgrep.sh` の `TARGET_DIRS`・`TARGET_EXTENSIONS`・`EXCLUDE_DIRS` と、この表を一緒に直す。
- 検査の前に、全ファイルをSemgrepの構文解析に通す。Semgrepは、どのルールの候補にもならない語だけのファイルを解析せず、構文エラーがあっても検出0件に見える。そのため、構文を解析できないファイルが1つでもあれば、失敗（30）にする。

## ルールと理由

ルールは、`security/semgrep/rules/python.yml` と `typescript.yml` にある。このアプリ（FastAPI、psycopgによるSQL、React、Playwright）で、実際に使う危険な書き方に絞った。コードを書くとき、レビューで見落としやすい書き方を、機械的に拾うことが目的。

| ルールID | 検出するもの | 選んだ理由 |
| --- | --- | --- |
| `py-sql-dynamic-string` | SQL文（`execute`・`executemany`・`exec_driver_sql`・`text` の第1引数）を、f文字列・`%`・`.format`・`+` で組み立てている。同じ関数内で変数に代入した場合も含む | アプリは、psycopgで、SQLを直接実行する。値は、プレースホルダ（`%s`）で渡すのが決まり。 |
| `py-shell-command-injection` | `subprocess` の `shell=True`、`os.system`、`os.popen`。固定の文字列だけのときは対象外 | 配布・検証のスクリプトが、外部コマンドを呼ぶ。シェルを通すと、値が命令になりうる。 |
| `py-eval-exec-dynamic` | 固定の文字列以外に対する `eval`・`exec` | 入力を、コードとして実行してしまう。 |
| `py-unsafe-deserialization` | `pickle`・`marshal` の読み込み、`yaml.load`（安全なLoaderを指定しないもの） | 任意のコードが実行されうる読み込み。 |
| `ts-react-dangerously-set-inner-html` | `dangerouslySetInnerHTML` に、固定の文字列以外を渡している | Reactで、XSSに直結する唯一の通常の書き方。 |
| `ts-dom-html-sink` | `innerHTML`・`outerHTML`・`insertAdjacentHTML`・`document.write` に、固定の文字列以外を渡している | DOMへのHTMLの書き込み（XSS）。 |
| `ts-eval-dynamic-code` | `eval`・`new Function`・文字列を渡す `setTimeout`／`setInterval`・`vm` の `run` 系 | 文字列をコードとして実行する。 |
| `ts-child-process-shell` | Nodeの `child_process` の `exec`・`execSync`（固定の文字列だけのときは対象外） | PlaywrightやNodeのスクリプトが、シェルを通して、値を命令にしうる。 |

### 検査の範囲と限界

このルールは、上の危険な書き方の**一部**だけを検査する。「Semgrepで問題なし」は、アプリが安全であることを意味しない。

- 見ないもの: 認証・認可、SSRF、パストラバーサル、セキュリティ設定、依存パッケージの脆弱性、Dockerfile・YAML・GitHub Actions・シェルスクリプトの内容。秘密情報は、Betterleaksが別に検査する（下記）。
- 追う範囲が狭い: `py-sql-dynamic-string` は、SQL文が、組み立てた文字列そのもの、または、同じ関数内で `変数 = 組み立てた文字列` と代入した変数のときだけ。関数をまたぐ流れ、辞書や属性に入れた値、ORMは追わない。
- SQLの再代入（固定の版で、実エンジンで確認した現在の挙動。`tests/semgrep/test_semgrep_scope.py`）: ルールは、代入の順序と分岐を追わない。同じ関数のなかで、組み立てた文字列を代入した変数を実行すると、後で固定の文字列に代入し直していても、検出する（誤検出側）。逆に、組み立てた文字列の代入が `if`・`for` の中にあるだけのときは、検出しない（見逃し側）。ルールは、この挙動に合わせて緩めていない。検出の内容を読んで判断する。
- `ts-child-process-shell` の範囲（実エンジンで確認した現在の挙動）: 指定子が `"child_process"` の `import * as`・名前付きimport（別名を含む）・`require`（分割代入と別名を含む）、`"node:child_process"` の別名なしの名前付きimport（`import { exec }`）と、名前が `child_process` の名前空間は、検出する。次は、**検出しない**: `"node:child_process"` の別名（名前付き・名前空間・`require`）、既定のimport（`import cp from "child_process"`）。`child_process` 以外のモジュールの `exec`、自前の `exec` 関数やオブジェクトの `exec`、`execFile`・`spawn` は、検出しない（意図どおり）。
- Pythonの `py-shell-command-injection` は、`import subprocess as sp`・`from subprocess import run as r`・`from os import system` などの別名や名前指定のimportも検出する。import していない同名の自前の関数は、検出しない。
- `eval` の扱いがPythonとTypeScriptで違う: `py-eval-exec-dynamic` は固定の文字列を除外するが、`ts-eval-dynamic-code` は固定の文字列の `eval` も検出する。
- `ts-eval-dynamic-code` が見ない書き方（実エンジンで確認）: `window.eval`・`globalThis.eval`・`(0, eval)(x)`、引数の変数にある文字列を渡す `setTimeout`／`setInterval`、`window.setTimeout("...")`・`globalThis.setTimeout("...")`。定数に束縛した固定の文字列を渡す `setTimeout(t, 1)` は、検出する。ルールは、これらのために広げていない。
- ファイル名: 対象のディレクトリの下の、空白・日本語・先頭がハイフンのファイル名とディレクトリ名は、構文チェック・検査・件数の一覧・検出の場所の表示で扱えることを、実エンジンで確かめた。**改行を含むファイル名は、対応を確かめておらず、対応するとは言わない。** 検査するファイルは、コマンドの引数で渡すため、ファイル数・パスの長さが `ARG_MAX` に近い大きなリポジトリは、確かめていない（このアプリの規模では想定しない。負荷の大きい試験は行っていない）。
- 誤検出がある: f文字列に、プレースホルダだけを入れる書き方（`IN (%s, %s)` の組み立て）や、利用者の入力ではない固定の名前を入れる書き方も検出する。区別は、検出の内容を読んで行う（確認記録の例が参考になる）。
- Semgrepのレジストリのルールセットは使わない。ネットワークなしで、同じ結果を再現するため。コミュニティのルールを足したいときは、内容を確認して、ローカルのファイルとして取り込み、フィクスチャを足す。
- Semgrepは、ファイル単位の解析（OSS版）。ファイルをまたぐデータの流れは追わない。

## ルールの検証（フィクスチャ）

`security/semgrep/fixtures/` に、架空の違反例と、検出しない例（対照）がある。実行はしない（Semgrepが読むだけ）。ルールごとに、両方が必要。

- 注釈: `# ruleid: <ルールID>`（TypeScriptは `// ruleid: <ルールID>`）は、**次の行**で、そのルールが検出されること、`ok:` は、検出されないことを示す。
- `bash scripts/semgrep.sh fixtures` は、検出された（ルールID、ファイル、行）が、注釈と過不足なく一致するか確かめる。検出されるはずなのに検出されない、検出されないはずなのに検出される、ルールに `ruleid` か `ok` の例がない、注釈が存在しないルールを指す、のどれかがあれば、失敗（30）にする。
- ルールを足す・変えるときは、フィクスチャを一緒に足す。足し忘れたルールは、検出が0件のまま通らない。

## 検出の確認記録

`security/semgrep/triage.tsv` は、初回のPR提出前の検査（2026-11-22、基準のコミット `dce0d1b`）の検出11件（`backend/tests/test_db_store.py` 2件、`backend/tests/test_dev_init.py` 3件、`db/verify_schema.py` 5件、`infra/edge/viewer-ip.test.js` 1件）を、Claude Codeが読んで判断した記録。列は、ルールID、ファイル、件数、理由。**参考の記録であり、ツールは読まない。** 検出の表示・終了コードには影響せず、検出を隠したり、承認や抑制を表したりしない。

- `bash scripts/semgrep.sh` は、検出をすべてそのまま表示し、1件でもあれば終了コード10を返す。
- 記録は、ファイルと件数だけで持つ。コードが変わって、検出が入れ替わっても、記録は自動では追従しない。レビュー担当は、検出の行を読み直して判断し、記録に頼らない。
- 記録の更新は、必須の手順ではない。検出の内容が今回の変更に関わるときだけ、理由を添えて書き直してよい。人の承認を条件にしない。
- 対応の優先度（直す・許容・後続課題）の判断が、アプリの仕様変更や既存検査の緩和を伴うときは、人に相談する。

## 独立したレビューでの使い方

ファイル作業の後、同じ作業コピーで起動する、新しいClaude Codeセッション（一般のレビュー）が、次を行う。[AGENTS.md](../AGENTS.md) の手順に含まれる。

1. 変更対象と受入条件を受け取り、差分を照合する。
2. `bash scripts/semgrep.sh` を実行し、終了コード（0・10・20・30）を、そのまま報告する。20（実行できない）は「未実行」、30は「失敗」と書き、成功扱いにしない。
3. 検出はすべて、該当の行を読んで、真の問題・誤検出・範囲外のどれかを判断して報告する（差分に関わる検出か、既存のコードの検出かも分ける）。確認記録は参考にとどめ、記録にあることを理由に省かない。実行できなかった（20）・失敗した（30）ときは、「未実行」「失敗」と報告する。
4. ルール・フィクスチャ・スクリプトを変えたときは、`bash scripts/semgrep.sh fixtures` と、`python3 -B -I -m unittest discover -s tests/semgrep` も実行する。
5. 秘密情報の検査 `bash scripts/scan-secrets.sh`（Betterleaks）は、これまでどおり実行する。Semgrepで置き換えない。
6. 文書だけの変更では、`git diff --check` と内容の整合性を確認する。

`claude -p` で起動するときは、レビュー担当に、書き込みと `.env` の読み取りを許さず、次のコマンドだけを許す。Bashの許可は、すべて完全一致で、ワイルドカードも任意の引数も含めない（`git diff` の `--output`・`--no-index` のような、書き込みや範囲外の読み取りになる引数を、許さないため）。

```bash
env -u ANTHROPIC_API_KEY claude -p --permission-mode dontAsk --no-session-persistence \
  --allowedTools Read Grep Glob \
    "Bash(git diff)" "Bash(git diff HEAD)" "Bash(git diff origin/main)" "Bash(git diff --cached)" \
    "Bash(git diff --check)" "Bash(git diff --stat)" "Bash(git status --short)" \
    "Bash(git log --oneline -10)" "Bash(git show --stat)" \
    "Bash(bash scripts/semgrep.sh)" "Bash(bash scripts/semgrep.sh fixtures)" \
    "Bash(bash scripts/scan-secrets.sh)" \
    "Bash(python3 -B -I -m unittest discover -s tests/semgrep)" \
  --disallowedTools Edit Write NotebookEdit "Read(**/.env)" "Read(**/.env.*)"
```

指摘への対応（修正やテスト）が必要なときは、レビュー担当に `Edit`・`Write` を許す別の起動にする。

### テスト不足のレビューには渡さない

`bash scripts/review-tests.sh` の、テスト不足のレビューは、この手順とは別の、独立した新しいセッションで行う。**Semgrepの実行結果、検出の確認記録（triage）、検査についての作業メモは、渡さない**（検証結果と作業メモを渡さない、既存の決まりのとおり。[testing-policy](testing-policy.md)）。`review-tests.sh` の許可するコマンド（読み取り、`git diff` など、`docker compose run ...`）は、変えていない。

- 保証するのは、2点だけ。作者の検査の結果と、確認記録の文面を、プロンプトへ入れないこと。新しい、読み取り専用の起動を保つこと。**ファイルシステム全体の技術的な隔離ではない。**
- 実装の内容（変更したファイルの一覧）には、`security/semgrep/triage.tsv` のパスが含まれうる。Readの許可は、そのファイルへも届きうる。これは、既存の範囲と権限のとおりで、結果の文面を渡すことを、新しく許すものではない。
- `review-tests.sh` が引き継ぐ、広い `git` の許可（上の `git diff` など）は、この文書の対象外で、変えていない。後続で見直す余地のあるリスクとして記録する（悪用できると確かめたものではない）。

## 参照した公式資料

プロジェクト独自の判断（版の固定、ローカルルール、Claude Codeへの委譲）と、公式の記述を区別する。2026-11-20（日本時間）に、Codexが本文を確認した。

- [Custom code review rules for Codex（OpenAI）](https://developers.openai.com/blog/custom-code-review-rules-for-codex): リポジトリのレビュー規約を、明示して書く。
- [Semgrep CLI reference](https://docs.semgrep.dev/cli-reference): `semgrep scan` の `--error`（検出で終了コード1）、`--strict`、エラー時の終了コード。
- [Running rules](https://docs.semgrep.dev/running-rules): ローカルのYAMLのルールを、`--config` で渡す。
- [Ignoring files, folders, and code](https://docs.semgrep.dev/ignoring-files-folders-code): 除外は、明示的に指定する。
- [Prerequisites](https://docs.semgrep.dev/prerequisites): LinuxとPythonの前提。

Claude Codeは、固定したコンテナで `semgrep scan --help`・`semgrep show dump-ast` を実行し、フラグと挙動を確認した。構文エラーのあるファイルが、候補のルールがないと報告されないこと（上の「対象と除外」）は、この確認で分かった。
