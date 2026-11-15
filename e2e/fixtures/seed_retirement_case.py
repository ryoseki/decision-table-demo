"""E2E専用のシード（設問の廃止のE2E用）。通常のデモ・開発用DBには適用しない。

compose.e2e.yaml の e2e-fixture サービスだけが実行する（E2E専用プロジェクトのDBに限る）。
作るもの（すべて架空）:
- 設問マスターの A13（参照するルールがない設問。設問文は、設問管理のモックが追加案で返す A13 と同じ）。
- 判定未登録の案件1件。受付と同じ保存（PostgresCaseStore.create_case）で作り、A13 に保存済みの回答 Yes を持つ。
  案件は、概要の目印（MARKER）で見つける。
アプリの本番経路にテスト専用のAPIは足さない。DBへ直接入れるのは、このE2E専用のコンテナだけ。
"""

import sys

from app.db_store import PostgresCaseStore, connect_from_env

MARKER = "（架空・E2E廃止用の未判定案件）"
QUESTION = ("A13", "api_key", "対象期間にAPIキーが失効されたか？")


def main() -> int:
    code, category, prompt = QUESTION
    with connect_from_env() as conn:
        conn.execute(
            "INSERT INTO review_questions (question_code, category, prompt,"
            " source_kind, source_field, revision)"
            " VALUES (%s, %s, %s, 'APIキーの履歴', 'APIキーの失効日時', 1)"
            " ON CONFLICT (question_code) DO NOTHING",
            (code, category, prompt),
        )
    store = PostgresCaseStore()
    case_id = store.create_case(
        {
            "summary": f"APIキーの失効について問い合わせが届いた。{MARKER}",
            "user": {"user_id": "456"},
            "inquiry": {"subject": "APIキーの失効", "body": "架空の問い合わせ。"},
        }
    )
    with connect_from_env() as conn:
        # 判定は登録しない（case_results は未判定のまま）。回答だけを保存済みにする。
        conn.execute(
            "INSERT INTO case_checks (case_id, question_code, answer)"
            " VALUES (%s, %s, 'yes')",
            (case_id, code),
        )
    print(f"E2E専用のシード: 廃止用の未判定案件 {case_id} を作りました（A13 の回答は Yes）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
