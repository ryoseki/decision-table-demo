# 管理者AIの試作: 判定ルールの追加案・削除案・更新案（モック・DBなし）

管理者が自然文で判定ルールの**追加案と削除案**を作り、Before／Afterの差分を確認して、「反映しますか？」にYes／Noで答える縦のスライスです。[設問の追加案・廃止案](admin-question-proposal-demo.md)と同じ流れで、操作の対象を判定ルールに広げています。AIの応答はモックで、DBなしで動きます。画面は `#/admin/rules`（ヘッダーの「判定ルール管理」）です。設問管理の画面とは別で、どちらを操作しているかを、見出しと「操作の対象: 判定ルール」（設問管理は「操作の対象: 設問」）で示します。

## このデモで決めたこと（デモ限定。本番の仕様ではない）

| 項目 | 内容 |
| --- | --- |
| 差分 | Before／Afterの全ルール一覧を出す。追加行は背景色と「追加」ラベル、削除するルールはBeforeで背景色と「削除」ラベル（Afterには出ない）。各行にルールID・版・結果・条件（設問＝Yes／No）を示す。現行のデモの判定ルール一覧だけは、ルール・版・結果の後にA01〜A12を列に並べ（A13以降の条件があるときだけ後ろに足す）、条件をYes／No、条件なしを `-` で示す |
| Yes／No | 設問の追加案と同じ。Yesだけが、デモの一覧へ反映する。Noは閉じるだけで変えない。承認前も変えない |
| 保存先 | バックエンドのメモリ。画面の再読み込みでは残り、バックエンドの再起動で初期状態（R01〜R03。開発用の架空ルールと同じ組み合わせの写し）に戻る。永続保存ではない |
| 分離 | このデモのルール一覧は、審査画面が照合に使うルール（`GET /api/rules`、案件の保存先）とは**別のデータ**。承認しても、審査画面のルール、案件、データベースは変わらない。審査画面への反映は、別のスライスで決める |
| 重複 | 次のどちらかを重複とし、422で拒否する。(1) 同じルールID。(2) 条件（設問ごとのYes／No。並び順は無関係）と結果がすべて一致する既存ルール。**条件が同じで結果が違うルールは重複にしない**（本番の優先順位や禁止は決めない） |
| ルールID | 追加できるIDは、これまでに使ったIDの最大番号の次（初期はR04）。削除したIDは再利用しない。R99の次はR100（桁数の上限は設けない） |
| 参照する設問 | 条件の設問は、デモの設問一覧に存在し、廃止済みでないこと。存在しない・廃止済みの設問を参照する案は422で拒否する |
| 結果 | 既存の結果コード（`account_deletion`・`account_suspension`・`caution`・`no_issue`）だけ。表示名ではなくコードで受け取る。条件のYes／Noも既存の値（`yes`・`no`）だけ |
| 競合 | 提案は、ルール一覧の版と設問一覧の版の**両方**を1つの基準として持つ（追加案・削除案とも）。提案を作ったあとに、どちらかが変わっていたとき、Yesは**部分反映せず**409で拒否する（提案は使い切る）。設問の変更が提案に無関係かどうかは判断しない（別の設問の追加でも拒否する。取り直して作り直す）。Noは従来どおり提案を破棄する。承認の確認から反映までの間に、設問の廃止は割り込めない（設問の決定とルールの決定は、共通のロックで1つずつ順に行う） |
| 設問の廃止との整合 | 設問を廃止するときの参照の確認は、案件の保存先のルールに加えて、このデモのルールも数える。同じルールID・版でも条件や結果が違えば別の行として両方を数え、全項目が一致する行だけ1件にする。追加したルールが参照する設問は、廃止できない。デモ側でルールを削除しても、案件の保存先に同じルールが残っていれば参照に数える（保守的） |

## 境界（`backend/app`、層は [ARCHITECTURE.md](../ARCHITECTURE.md)）

| 境界 | モジュール | 役割 |
| --- | --- | --- |
| 提案の生成 | `rule_proposer`（`MockRuleProposer`）、`bedrock_proposer`（`BedrockRuleProposer`） | 依頼から、AIの生の出力（辞書）を返す。既定はモック。Bedrockの実接続は、同じ `generate` の形で差し替える（[bedrock-connection.md](bedrock-connection.md)）。生成元は属性 `source` で申告する |
| 検証 | `rule_validation` | 純粋な関数。形式・結果コード・条件・参照する設問・重複・ID・削除の対象を確かめ、検証済みの提案（基準のルール一覧とその版、検証に使った設問一覧の版つき）にする |
| 承認と保存 | `rule_store`（`InMemoryRuleStore`） | 検証済みの提案と、デモのルール一覧を持つ。承認（版の比較と反映）をロックの中で1回にまとめて行う |
| HTTP | `rule_admin` | 入力の型、ステータスへの対応。設問の一覧を `question_store` から読み取り、検証に渡す |

## API

| エンドポイント | 内容 |
| --- | --- |
| `GET /api/admin/rules` | デモのルール一覧（`revision`、`rules`、`mock`、`persistence`、`source`）。ルールは `rule_id`、`revision`、`result_code`、`conditions` |
| `POST /api/admin/rule-proposals` | `{prompt}` から追加案・削除案・更新案を作る（201）。応答は `proposal_id`、`kind`（`add`／`remove`／`update`）、`before`、`after`、`added`（追加案）、`removed`（削除案）、または `target`・`updated`・`added_conditions`（更新案）。**一覧は変えない** |
| `POST /api/admin/rule-proposals/{id}/decision` | `{decision: "yes" \| "no"}`。Yesだけが反映する。応答は `applied` と最新の一覧 |

| ステータス | 場合 |
| --- | --- |
| 422 | 依頼が不正／AIの出力が検証を満たさない（形式、存在しない・廃止済みの設問、定義にない結果コード、重複、IDが次の番号でない、削除の対象がない）／`decision` が不正。現行データは変えない |
| 502 | AIの応答を得られない。実接続のときは、原因ごとの固定の文言 |
| 504 | AIの応答がタイムアウトした（実接続） |
| 404 | 提案が未知、または承認・却下済み |
| 409 | 提案の後に、ルール一覧または設問一覧の版が変わっている。反映しない（提案は使い切る） |

- 検証は、提案の生成の中で、登録の前に必ず通す（承認の入口を、サーバーが持つ検証済みの提案のIDだけにする）。
- 依頼の例（モック）: 「設問A09がYes、設問A10がNoのとき注意とするルールを追加して」→ ルールR04（条件 `A09=yes`・`A10=no`、結果 `caution`）。「ルールR03を削除して」→ R03の削除案。「ルール」を含まない依頼、条件のない追加の依頼、IDのない削除の依頼は、検証で422。結果の名前「アカウント削除」は削除の操作と見なさない（「…アカウント削除とするルールを追加して」は追加、「アカウント削除のルールR03を削除して」は削除）。存在しない設問（例: 「設問A99がYes…」）は、出力にそのまま入り、検証で422になる。

## 更新案（既存ルールへの条件の追加）

「R04にA09がYesを追加して」のように、**指定した既存ルールに条件を足す**依頼は、新しいルールを作らず、同じIDのまま更新する案になる（生成の出力は `{"operation": "update_rule", "rule_id": "R04", "add_conditions": {"A09": "yes"}}`）。対象は、判定ルールの**条件の追加だけ**で、設問自体の編集、ルールの判定結果の変更、既存の条件の削除・書き換え、公開設定の変更は扱わない。

- 保つもの: ルールID、既存の条件、結果、他のルール、ルールの並びと総数。更新したルールの版（`revision`）だけ1つ進む。
- 差分: Before／Afterの同じIDの行に「更新」、Afterで足した条件に「（追加）」を示す。Yesで反映し、Noでは承認済みのルールを変えない。
- 拒否（422。現行データは変えない）:
  - 対象のルール・設問がない、廃止済みの設問を参照する。
  - 依頼の指定が曖昧（複数のルールIDがある、同じ設問にYesとNoがある）。ルールIDと「追加」に加えて設問IDまたは「条件」があるのに、更新と読めず（例: 「R04にA09のYesを追加して」）、新規作成の明示（「新しいルールとして」など）もない依頼は、新規追加の案も反映しない。ルールIDだけを含む依頼（例: 「R04を追加」）は対象外。小文字のID（「r04にa09がyesを追加して」「R04にa09がyesを追加して」「r04にA09がYesを追加して」）は正式な入力として受理せず、既存ルールを指す疑いのある曖昧な依頼として、新規追加の案も更新の案も反映しない（理由つき）。生成が新規追加を返した場合も、定型依頼の再利用の場合も同じ。
  - 同じ条件を追加済み、同じ設問に違う回答の条件が既にある、更新後の条件と結果が他のルールと同じになる（重複）。
  - **生成の出力が依頼と食い違う**（更新の依頼を新規追加で返した、対象のルールや追加する条件が依頼と違う、依頼に対象がないのに更新を返した）。依頼の文面は、サーバーが読み直して突き合わせる。定型依頼の再利用が有効なときも、保存済みの操作を同じ検証に通す。
- 409: 提案の後に、ルール一覧（対象の版を含む）または設問一覧の版が変わっていたとき。部分反映しない。
- 保存（DB）: 共有設定の新しい版として確定し、`decision_rules` に更新後の版の行を足す（旧い版の行は残る）。判定未登録の案件は更新後のルールで照合し、保存済みの判定が持つ採用ルールの版・結果は変わらない。
- 定型文: Yesで反映した更新は、対象のIDと追加した条件を、定型文（`update_rule`）として登録する。
- 検証していない範囲: 公開環境（実Bedrock・公開経路）での有効化と実機の確認は未実施。

## 層ごとの確認

| 層 | 確認 | テスト |
| --- | --- | --- |
| API契約（DBなし） | 存在しない・廃止済みの設問を参照する案を拒否。定義にない結果コード・不正な条件を拒否 | `backend/tests/test_rule_admin.py`: `TestValidation`、`TestAddProposal`（`test_rejects_a_rule_that_refers_to_...`） |
| | 重複（同じID、条件と結果が同じ。並び順は無関係）を拒否。結果が違えば重複にしない | 同: `test_rejects_the_same_conditions_and_result_in_any_order`、`test_same_conditions_with_a_different_result_is_not_a_duplicate` ほか |
| | 承認前・Noは現行データを変えず、Yesだけ反映。承認の要求の内容は無視 | 同: `TestAddProposal`、`TestAddDecision`、`TestRemoval` |
| | 基準データ（ルール一覧の版・設問一覧の版の両方）が変わっていたときは、部分反映せず409。提案は使い切る。Noは破棄 | 同: `test_refuses_a_conflict_when_the_base_data_changed_without_partial_apply`、`test_refuses_when_a_question_was_retired_after_the_proposal`、`test_refuses_an_add_when_any_question_change_followed_the_proposal`、`test_refuses_a_removal_when_a_question_changed_after_the_proposal`、`test_no_still_discards_a_proposal_whose_question_base_changed` |
| | ルール承認と設問の廃止の直列化（承認の途中に廃止が割り込めない。待機の検知はイベントで、スリープなし） | 同: `TestRuleApprovalIsSerializedWithQuestionRetirement`、`TestMainWiring` |
| | 審査画面のルール・案件を変えない（分離） | 同: `TestIsolationAndQuestionIntegrity` |
| | 設問の廃止との整合（追加したルールが参照する設問は廃止できない。後から参照されても拒否） | 同: `test_a_question_that_an_added_rule_refers_to_cannot_be_retired`、`test_a_rule_added_after_the_retirement_proposal_blocks_yes`。既存: `backend/tests/test_question_retirement.py` |
| フロント単体（APIモック） | 追加案・削除案の差分、検証エラー・競合・通信エラーの表示、どちらを操作しているか、Yes／Noの挙動（設問の追加案と同じ） | `frontend/src/RuleAdmin.test.tsx`、`frontend/src/rulesApi.test.ts`、`frontend/src/App.test.tsx`、`frontend/src/route.test.ts` |
| E2E（1本） | 存在しない設問を参照する案は検証エラーで現行データが変わらない → 追加案の差分を確認 → No で変わらない → Yes で反映（再読み込みでも残り、審査画面のルールは変わらない） | `e2e/tests/rule-admin.spec.ts` |
| 更新案: API契約（DBなし・生成はスタブ） | 例の依頼でR04が対象（R05は作らない）、Yes／No、他のルールと既存の条件の保持、新規作成として返された出力・対象や条件の食い違い・存在しないルール／設問・廃止済みの設問・追加済み・重複・版の競合の拒否、定型依頼の再利用でも検証、曖昧な小文字IDの依頼が新設に落ちない（生成が新規追加を返す場合・定型依頼の場合を含む。明示的な新規追加と「R04を追加」は通る）、連続する2回の更新（版3・同じID・総数不変）、複数の条件の同時追加（全件が新規なら成功。一部だけ既存なら、新規が先でも後でも全体を拒否）、依頼との照合なしの形式の拒否（ID・設問ID・yes／no以外・非dict・欠落） | `backend/tests/test_rule_update.py`（`TestUpdateIntent`、`TestValidation`、`TestUpdateProposal`、`TestUpdateDecision`、`TestUpdateWithTemplateReuse`）、`backend/tests/test_bedrock_proposer.py`（`test_update_request_...`、`test_a_new_rule_returned_for_an_update_request_...`） |
| 更新案: フロント単体 | R04の更新とA09=Yesの追加差分、Yesで更新済みの一覧、Noで元の一覧、拒否の理由、競合 | `frontend/src/RuleAdminUpdate.test.tsx`、`frontend/src/rulesApi.test.ts` |
| 更新案: E2E | 例の依頼 → 差分 → No → Yes。R04だけに条件が追加され、R05は作られない。拒否は理由が出て変わらない。前提のR04は、テスト自身がHTTPで準備する（なければ追加案を作って承認する） | `e2e/tests/rule-admin.spec.ts`（2本目。R04は1本目が反映する） |
| 更新案: DB統合 | DB構成のHTTP経路（提案 → 決定）で、承認後に別のサーバー・接続から同じIDの更新を読める。判定未登録の案件は、取得した最新ルールを既存の照合ロジックで照合し、登録済みの判定は保持。保存済みの提案（pending）を保存内容から作り直せ、対象が消えていれば版が一致しても拒否。版の競合は変えない。定型文の保存。連続する2回の更新で版1・2・3の行がそれぞれ残り最新と一致。複数の条件の同時追加は新しい版の1行だけを足し、一部だけ既存の更新は（提案時・承認時とも）全体を拒否して何も増やさない | `backend/tests/test_db_store.py`（`test_rule_update_over_http_is_read_back_...`、`test_unregistered_case_matches_the_updated_rule_...`、`test_pending_update_is_rebuilt_from_the_stored_payload`、`test_update_is_refused_when_the_target_is_gone_...`、`test_sequential_rule_updates_...`、`test_updating_several_conditions_at_once_...`、`test_partly_existing_conditions_over_http_...`、`test_several_conditions_update_is_refused_when_...`） |
| 構造検査 | 依存の向き | `lint-imports`（契約は `backend/pyproject.toml`）、`npm run lint` |

## 未決・未確認（確定していない）

- 本番の仕様: ルールの過去版の保持・廃止と物理削除、同じ条件で結果が異なるルールの優先順位や禁止、条件の追加以外のルールの編集（結果の変更、条件の削除・書き換え）、承認の担当と運用は、確定していない。このデモの「削除」は、デモの一覧から外すだけで、過去版を残すかどうかを決めていない。
- 画面で動いている照合表示（審査画面）への反映は、別のスライスで決める。このデモのルールは、案件の照合に使われない。
- DBへの永続保存は、この範囲に含まない。Bedrockの実接続は、設定で選べる（既定はモック。[bedrock-connection.md](bedrock-connection.md)）。実際のBedrockでの確認は、人が行う（未確認）。複数のバックエンド（Lambdaの複数インスタンスなど）では、メモリが共有されない。
- 設問の一覧とルールの一覧は別のメモリ（別のロック）だが、設問の決定（追加・廃止のYes／No）とルールの決定は、共通のロック（`app.state.admin_lock`）で1つずつ順に行う。ルール承認の途中に、設問の廃止は割り込めない（割り込もうとした廃止は待たされ、反映済みのルールの参照を見て409になる）。この直列化は、1つのバックエンドプロセスの中だけで成り立つ。複数プロセス・複数インスタンスにまたがる整合は、このデモでは扱わない。本番のDBでは、設問とルールの版を1つのトランザクション（または楽観的な競合検出）で扱う設計が別に必要。
- 管理者の認証・権限は、この試作にない。
