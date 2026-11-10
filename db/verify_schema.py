"""docs/decision-support-schema.md のDDL案を、ローカルのPostgreSQLで検証する。

最終適用ではなく互換性の確認用。結果はAurora DSQLへの適合の証拠にならない。
"""

import json
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import errors

DOCS = Path(os.environ.get("DOCS_DIR", "/repo/docs"))
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""), flush=True)


def connect() -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "disable"),
        autocommit=True,
    )


def extract_ddl() -> list[str]:
    text = (DOCS / "decision-support-schema.md").read_text(encoding="utf-8")
    block = re.search(r"```sql\n(.*?)```", text, re.S).group(1)
    return [s.strip() for s in block.split(";") if s.strip()]


def expect_error(conn, name, exc, sql, params=None):
    try:
        with conn.transaction():
            conn.execute(sql, params)
        record(name, False, "エラーにならなかった")
    except exc:
        record(name, True)
    except Exception as e:  # noqa: BLE001
        record(name, False, f"想定外: {type(e).__name__}")


def expect_ok(conn, name, sql, params=None):
    try:
        with conn.transaction():
            conn.execute(sql, params)
        record(name, True)
    except Exception as e:  # noqa: BLE001
        record(name, False, f"{type(e).__name__}: {e}")


def save(conn, case_id, scenario, result_code, rule_id, checks, now="2026-10-20T13:05:00+09:00"):
    """［判定する］の保存処理案: 1トランザクションでupsertと置き換え。"""
    with conn.transaction():
        conn.execute(
            """INSERT INTO case_results (case_id, scenario, result_code, rule_id, recorded_by, recorded_at)
               VALUES (%s, %s, %s, %s, 'OP-1', %s)
               ON CONFLICT (case_id) DO UPDATE SET
                 scenario = EXCLUDED.scenario, result_code = EXCLUDED.result_code,
                 rule_id = EXCLUDED.rule_id, recorded_by = EXCLUDED.recorded_by,
                 recorded_at = EXCLUDED.recorded_at""",
            (case_id, scenario, result_code, rule_id, now),
        )
        conn.execute("DELETE FROM case_checks WHERE case_id = %s", (case_id,))
        for q, ans in checks:
            # 未回答（answer=None）は回答者・回答日時もNULLにする（スキーマ案のCHECKと一致）
            by, at = ("OP-1", now) if ans is not None else (None, None)
            conn.execute(
                """INSERT INTO case_checks (case_id, question_code, answer, answered_by, answered_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (case_id, q, ans, by, at),
            )


def load_state(conn, case_id):
    res = conn.execute(
        "SELECT result_code, rule_id FROM case_results WHERE case_id = %s", (case_id,)
    ).fetchone()
    checks = conn.execute(
        "SELECT question_code, answer FROM case_checks WHERE case_id = %s ORDER BY question_code",
        (case_id,),
    ).fetchall()
    return res, checks


def load_checks_detail(case_id):
    """別の接続で回答の全項目を読み直す（再表示の確認用）。"""
    other = connect()
    try:
        return other.execute(
            "SELECT question_code, answer, answered_by, answered_at IS NULL "
            "FROM case_checks WHERE case_id = %s ORDER BY question_code",
            (case_id,),
        ).fetchall()
    finally:
        other.close()


def main() -> int:
    conn = connect()
    version = conn.execute("SHOW server_version").fetchone()[0]
    print(f"対象: ローカルPostgreSQL {version}（Aurora DSQLではない）", flush=True)

    ddl = extract_ddl()
    record("DDL案を文書から取り出せた（5テーブル）", len(ddl) == 5, f"{len(ddl)}文")

    banned = re.compile(r"\b(SERIAL|TRIGGER|TEMP|TEMPORARY|EXTENSION|TRUNCATE|TABLESPACE|CREATE TYPE|PLPGSQL)\b", re.I)
    plain_index = re.compile(r"CREATE\s+(UNIQUE\s+)?INDEX\s+(?!ASYNC)", re.I)
    bad = [s.split("(")[0] for s in ddl if banned.search(s) or plain_index.search(s)]
    record("静的確認: DSQLが対応しないと公式資料にある構文を含まない", not bad, ", ".join(bad))

    for stmt in ddl:
        name = re.match(r"CREATE TABLE (\w+)", stmt).group(1)
        expect_ok(conn, f"DDL適用（1トランザクション1文）: {name}", stmt)

    sample = json.loads((DOCS / "decision-support-sample.json").read_text(encoding="utf-8"))
    for table in ["review_cases", "review_questions", "decision_rules", "case_results", "case_checks"]:
        rows = sample[table]
        try:
            with conn.transaction():
                for r in rows:
                    cols = list(r)
                    vals = [json.dumps(r[c]) if c.endswith("_data") or c == "conditions" else r[c] for c in cols]
                    conn.execute(
                        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})", vals
                    )
            n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            record(f"架空データ登録: {table}", n == len(rows), f"{n}行")
        except Exception as e:  # noqa: BLE001
            record(f"架空データ登録: {table}", False, f"{type(e).__name__}: {e}")

    ins_case = ("INSERT INTO review_cases (case_id, scenario, summary, user_data, inquiry_data) "
                "VALUES (%s, %s, 's', %s, '{}')")
    expect_error(conn, "制約: case_idが空", errors.CheckViolation, ins_case, ("  ", "credits", "{}"))
    expect_error(conn, "制約: scenarioが未定義値", errors.CheckViolation, ins_case, ("X1", "other", "{}"))
    expect_error(conn, "制約: user_dataが配列", errors.CheckViolation, ins_case, ("X2", "credits", "[]"))
    expect_error(conn, "制約: case_id重複", errors.UniqueViolation, ins_case, ("CASE-C", "credits", "{}"))
    expect_error(conn, "制約: 期間が片方だけ", errors.CheckViolation,
                 "INSERT INTO review_cases (case_id, scenario, summary, user_data, inquiry_data, period_start_at) "
                 "VALUES ('X3','credits','s','{}','{}', now())")
    expect_error(conn, "制約: 期間の開始が終了以降", errors.CheckViolation,
                 "INSERT INTO review_cases (case_id, scenario, summary, user_data, inquiry_data, period_start_at, period_end_at) "
                 "VALUES ('X4','credits','s','{}','{}', now(), now())")
    expect_error(conn, "制約: conditionsが空オブジェクト", errors.CheckViolation,
                 "INSERT INTO decision_rules VALUES ('R-X','credits','demo_result','{}')")
    expect_error(conn, "制約: conditionsが配列", errors.CheckViolation,
                 "INSERT INTO decision_rules VALUES ('R-Y','credits','demo_result','[1]')")
    ins_res = ("INSERT INTO case_results (case_id, scenario, result_code, rule_id, recorded_by, recorded_at) "
               "VALUES (%s, %s, %s, %s, 'OP-1', now())")
    expect_error(conn, "外部キー: 存在しない案件の結果", errors.ForeignKeyViolation, ins_res, ("NOPE", "credits", "demo_result", None))
    conn.execute("INSERT INTO review_cases (case_id, scenario, summary, user_data, inquiry_data) "
                 "VALUES ('FK-C','credits','s','{}','{}'), ('FK-A','account_api','s','{}','{}')")
    expect_error(conn, "外部キー: 案件とシナリオが不一致", errors.ForeignKeyViolation, ins_res, ("FK-A", "credits", "x", None))
    expect_error(conn, "外部キー: ルールと結果コードが不一致", errors.ForeignKeyViolation,
                 ins_res, ("FK-C", "credits", "other_result", "DEMO-RULE-1"))
    expect_error(conn, "外部キー: ルールとシナリオが不一致", errors.ForeignKeyViolation,
                 ins_res, ("FK-A", "account_api", "demo_result", "DEMO-RULE-1"))
    expect_ok(conn, "rule_idがNULLなら任意のresult_codeで保存できる（MATCH SIMPLE）",
              ins_res, ("FK-C", "credits", "any_manual_code", None))
    ins_chk = ("INSERT INTO case_checks (case_id, question_code, answer, answered_by, answered_at) "
               "VALUES (%s, %s, %s, %s, now())")
    # FK-Aはreview_casesにあり、case_resultsにない（上の結果登録はすべて失敗している）。親の結果行だけが欠けた状態を確かめる。
    expect_error(conn, "外部キー: 結果のない案件の回答", errors.ForeignKeyViolation, ins_chk, ("FK-A", "A03", "yes", "OP-1"))
    expect_error(conn, "外部キー: 存在しない設問", errors.ForeignKeyViolation, ins_chk, ("CASE-A", "ZZ9", "yes", "OP-1"))
    expect_error(conn, "制約: answerが未定義値", errors.CheckViolation, ins_chk, ("CASE-A", "A03", "maybe", "OP-1"))
    expect_error(conn, "制約: answerがあり回答者がNULL", errors.CheckViolation, ins_chk, ("CASE-A", "A03", "yes", None))
    expect_error(conn, "制約: 同じ案件・設問の回答が重複", errors.UniqueViolation, ins_chk, ("CASE-A", "A03", "yes", "OP-1"))

    # 一括保存・再表示・再判定・ロールバック
    conn.execute("INSERT INTO review_cases (case_id, scenario, summary, user_data, inquiry_data) "
                 "VALUES ('SAVE-1','account_api','s','{\"user_id\":\"9\"}','{}')")
    save(conn, "SAVE-1", "account_api", "demo_manual_result", None, [("A03", "yes"), ("A09", "pending")])
    res, checks = load_state(conn, "SAVE-1")
    record("保存→再表示: 結果と確認待ちを含む回答が戻る",
           res == ("demo_manual_result", None) and checks == [("A03", "yes"), ("A09", "pending")])

    save(conn, "SAVE-1", "account_api", "other_manual", None, [("A09", "no")])
    res, checks = load_state(conn, "SAVE-1")
    record("再判定: 最新のみ保持（結果の置換、除いた回答の消去、履歴なし）",
           res == ("other_manual", None) and checks == [("A09", "no")])

    save(conn, "SAVE-1", "account_api", "demo_manual_result", None, [])
    res, checks = load_state(conn, "SAVE-1")
    record("回答0件でも結果を保存できる", res == ("demo_manual_result", None) and checks == [])

    # 未回答（NULL）と確認待ち（pending）を同じ保存で扱い、別接続から再表示する
    save(conn, "SAVE-1", "account_api", "demo_manual_result", None, [("A03", None), ("A09", "pending")])
    record("未回答NULLと確認待ちを同一保存し、別接続から再表示できる",
           load_checks_detail("SAVE-1") == [("A03", None, None, True), ("A09", "pending", "OP-1", False)])

    save(conn, "SAVE-1", "account_api", "demo_manual_result", None, [("A03", "yes")])
    save(conn, "SAVE-1", "account_api", "demo_manual_result", None, [("A03", None)])
    record("訂正: yesからNULLへ戻すと回答者・回答日時もNULLで再表示される",
           load_checks_detail("SAVE-1") == [("A03", None, None, True)])

    save(conn, "SAVE-1", "account_api", "keep_me", None, [("A03", "yes"), ("A09", "no")])
    before = load_state(conn, "SAVE-1")
    try:
        save(conn, "SAVE-1", "account_api", "must_not_persist", None, [("A03", "no"), ("ZZ9", "yes")])
        record("ロールバック: 子の失敗で親子とも元に戻る", False, "エラーにならなかった")
    except errors.ForeignKeyViolation:
        record("ロールバック: 子の失敗で親子とも元に戻る", load_state(conn, "SAVE-1") == before)

    total = len(results)
    failed = [r for r in results if not r[1]]
    print(f"\n結果: {total - len(failed)}/{total} 件成功。ローカルPostgreSQLのみ。Aurora DSQLの動作は未確認。", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
