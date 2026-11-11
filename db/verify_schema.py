"""docs/decision-support-schema.md のDDL案を、ローカルのPostgreSQLで検証する。

最終適用ではなく互換性の確認用。結果はAurora DSQLへの適合の証拠にならない。
DB_SCHEMAを指定すると、その専用スキーマの中で検証し、最後に削除する。
"""

import json
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import errors

DOCS = Path(os.environ.get("DOCS_DIR", "/repo/docs"))
SCHEMA = os.environ.get("DB_SCHEMA", "")  # 指定すると専用スキーマを作って検証し、最後に削除する
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
        # 実DSQLでは専用スキーマの中だけで検証し、既存のテーブルに触れない。
        options=f"-c search_path={SCHEMA}" if SCHEMA else None,
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


def copy_questions(conn, case_id):
    """受付時の処理案: マスターの全設問を案件へコピーして固定する。"""
    with conn.transaction():
        conn.execute(
            """INSERT INTO case_questions (case_id, question_code, revision, category, prompt,
                                           source_kind, source_field)
               SELECT %s, question_code, revision, category, prompt, source_kind, source_field
               FROM review_questions""",
            (case_id,),
        )


def intake_case(conn, case_id, fetch, user_data='{"user_id": "456"}'):
    """受付の処理案（案件を先に保存し、取得を後にする）。

    1回目の保存（1トランザクション）: 案件・全設問のコピー・未判定の判定結果・A09の回答（pending）。
    取得（トランザクションの外）: 例外なら失敗でpendingのまま。値は2回目の保存でDBのCHECKが検査する（yes・no以外は2回目の保存の失敗として扱う）。
    2回目の保存（1トランザクション）: 成功したときだけA09のanswerとautomated_answerを更新する。
    更新が失敗してもpendingのまま残し、再取得しない。
    """
    with conn.transaction():
        conn.execute(
            "INSERT INTO review_cases (case_id, summary, user_data, inquiry_data) VALUES (%s, 's', %s, %s)",
            (case_id, user_data, '{"subject": "s", "body": "b"}'),
        )
        copy_questions(conn, case_id)
        conn.execute("INSERT INTO case_results (case_id) VALUES (%s)", (case_id,))
        conn.execute(
            "INSERT INTO case_checks (case_id, question_code, answer) VALUES (%s, 'A09', 'pending')",
            (case_id,),
        )
    try:
        value = fetch()
    except Exception:  # noqa: BLE001 取得失敗はpendingのまま
        return
    try:
        with conn.transaction():
            conn.execute(
                "UPDATE case_checks SET answer = %s, automated_answer = %s "
                "WHERE case_id = %s AND question_code = 'A09'",
                (value, value, case_id),
            )
    except Exception:  # noqa: BLE001 2回目の保存の失敗もpendingのまま
        return


def save(conn, case_id, result_code, rule, checks, now="2026-10-20T13:05:00+09:00"):
    """［判定する］の保存処理案: 1トランザクションで結果を更新し、回答をanswerだけ更新する。

    回答を空に戻す操作は許さないため、回答の行は削除しない。automated_answerは触らない。
    """
    rule_id, rule_rev = rule if rule else (None, None)
    with conn.transaction():
        conn.execute(
            """INSERT INTO case_results (case_id, result_code, rule_id, rule_revision,
                                         recorded_by, recorded_at)
               VALUES (%s, %s, %s, %s, 'OP-1', %s)
               ON CONFLICT (case_id) DO UPDATE SET
                 result_code = EXCLUDED.result_code, rule_id = EXCLUDED.rule_id,
                 rule_revision = EXCLUDED.rule_revision,
                 recorded_by = EXCLUDED.recorded_by, recorded_at = EXCLUDED.recorded_at""",
            (case_id, result_code, rule_id, rule_rev, now),
        )
        for q, ans in checks.items():
            conn.execute(
                """INSERT INTO case_checks (case_id, question_code, answer) VALUES (%s, %s, %s)
                   ON CONFLICT (case_id, question_code) DO UPDATE SET answer = EXCLUDED.answer""",
                (case_id, q, ans),
            )


def load_state(conn, case_id):
    res = conn.execute(
        "SELECT result_code, rule_id, rule_revision FROM case_results WHERE case_id = %s",
        (case_id,),
    ).fetchone()
    checks = conn.execute(
        "SELECT question_code, answer, automated_answer FROM case_checks WHERE case_id = %s ORDER BY question_code",
        (case_id,),
    ).fetchall()
    return res, checks


def load_checks_other_connection(case_id):
    """別の接続で回答を読み直す（再表示の確認用）。"""
    other = connect()
    try:
        return other.execute(
            "SELECT question_code, answer, automated_answer FROM case_checks WHERE case_id = %s ORDER BY question_code",
            (case_id,),
        ).fetchall()
    finally:
        other.close()


def timeout_fetch():
    raise TimeoutError("モックの応答なし（検証用）")


TABLES_REVERSE = ["case_checks", "case_results", "case_questions", "decision_rules", "review_questions", "review_cases"]


def main() -> int:
    if SCHEMA:
        if not re.fullmatch(r"dsql_verify_[0-9a-f]{8}", SCHEMA):
            print("DB_SCHEMAは dsql_verify_<8桁の16進> の形式だけ受け付ける。", file=sys.stderr)
            return 2
        admin = psycopg.connect(
            host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
            sslmode=os.environ.get("DB_SSLMODE", "disable"), autocommit=True)
        admin.execute(f"CREATE SCHEMA {SCHEMA}")
        admin.close()
    try:
        return run()
    finally:
        if SCHEMA:
            cleanup()


def cleanup() -> None:
    """専用スキーマの表とスキーマだけを削除する（1トランザクション1文）。"""
    conn = psycopg.connect(
        host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "disable"), autocommit=True)
    ok = True
    for tbl in TABLES_REVERSE:
        try:
            conn.execute(f"DROP TABLE IF EXISTS {SCHEMA}.{tbl}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"後片付け失敗: {tbl}: {type(e).__name__}: {e}", file=sys.stderr)
    try:
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"後片付け失敗: スキーマ: {type(e).__name__}: {e}", file=sys.stderr)
    print(f"後片付け: {'完了' if ok else '一部失敗。専用スキーマ ' + SCHEMA + ' を手動で確認'}", flush=True)
    conn.close()


def run() -> int:
    conn = connect()
    if SCHEMA:
        # search_pathが効いていないと、表が既定のスキーマに作られてしまう。DDLの前に必ず確認して止める。
        current = conn.execute("SELECT current_schema()").fetchone()[0]
        if current != SCHEMA:
            print(f"中止: 現在のスキーマが{current}で、専用スキーマ{SCHEMA}ではありません。何も作成していません。", file=sys.stderr)
            return 3
    version = conn.execute("SHOW server_version").fetchone()[0]
    print(f"対象: PostgreSQL {version}" + ("（DB_SCHEMA指定あり）" if SCHEMA else "（ローカル。Aurora DSQLではない）"), flush=True)

    ddl = extract_ddl()
    record("DDL案を文書から取り出せた（6テーブル）", len(ddl) == 6, f"{len(ddl)}文")

    banned = re.compile(r"\b(SERIAL|TRIGGER|TEMP|TEMPORARY|EXTENSION|TRUNCATE|TABLESPACE|CREATE TYPE|PLPGSQL)\b", re.I)
    plain_index = re.compile(r"CREATE\s+(UNIQUE\s+)?INDEX\s+(?!ASYNC)", re.I)
    bad = [s.split("(")[0] for s in ddl if banned.search(s) or plain_index.search(s)]
    record("静的確認: DSQLが対応しないと公式資料にある構文を含まない", not bad, ", ".join(bad))

    for stmt in ddl:
        name = re.match(r"CREATE TABLE (\w+)", stmt).group(1)
        expect_ok(conn, f"DDL適用（1トランザクション1文）: {name}", stmt)

    sample = json.loads((DOCS / "decision-support-sample.json").read_text(encoding="utf-8"))
    for table in ["review_cases", "review_questions", "decision_rules", "case_questions", "case_results", "case_checks"]:
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

    ins_case = "INSERT INTO review_cases (case_id, summary, user_data, inquiry_data) VALUES (%s, 's', %s, '{}')"
    expect_error(conn, "制約: case_idが空", errors.CheckViolation, ins_case, ("  ", "{}"))
    expect_error(conn, "制約: user_dataが配列", errors.CheckViolation, ins_case, ("X2", "[]"))
    expect_error(conn, "制約: case_id重複", errors.UniqueViolation, ins_case, ("CASE-A", "{}"))
    expect_error(conn, "制約: 期間が片方だけ", errors.CheckViolation,
                 "INSERT INTO review_cases (case_id, summary, user_data, inquiry_data, period_start_at) "
                 "VALUES ('X3','s','{}','{}', now())")
    expect_error(conn, "制約: 期間の開始が終了以降", errors.CheckViolation,
                 "INSERT INTO review_cases (case_id, summary, user_data, inquiry_data, period_start_at, period_end_at) "
                 "VALUES ('X4','s','{}','{}', now(), now())")
    expect_error(conn, "制約: conditionsが空オブジェクト", errors.CheckViolation,
                 "INSERT INTO decision_rules VALUES ('R-X',1,'demo_result','{}',now())")
    expect_error(conn, "制約: conditionsが配列", errors.CheckViolation,
                 "INSERT INTO decision_rules VALUES ('R-Y',1,'demo_result','[1]',now())")
    expect_error(conn, "制約: 同じrule_idとrevisionは追加できない", errors.UniqueViolation,
                 "INSERT INTO decision_rules VALUES ('DEMO-RULE-1',1,'demo_result','{\"A03\":\"yes\"}',now())")
    expect_ok(conn, "ルールの改訂: 新しいrevisionの行を追加できる（旧版は残る）",
              "INSERT INTO decision_rules VALUES ('DEMO-RULE-1',2,'demo_result','{\"A03\":\"yes\"}','2026-10-27T09:00:00+09:00')")

    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name IN ('case_results', 'case_checks')").fetchall()}
    record("列: supplement_dataは存在せず、automated_answerが存在する",
           "supplement_data" not in cols and "automated_answer" in cols)

    # case_results: 未判定の行と、3列の全部空か全部入りの制約
    new_case = "INSERT INTO review_cases (case_id, summary, user_data, inquiry_data) VALUES (%s,'s','{}','{}')"
    for cid in ("FK-1", "FK-2", "FK-3", "FK-4", "FK-5", "FK-6", "FK-7", "FK-8"):
        conn.execute(new_case, (cid,))
    ins_res = ("INSERT INTO case_results (case_id, result_code, rule_id, rule_revision, recorded_by, recorded_at) "
               "VALUES (%s, %s, %s, %s, 'OP-1', now())")
    expect_error(conn, "外部キー: 存在しない案件の結果", errors.ForeignKeyViolation, ins_res, ("NOPE", "demo_result", None, None))
    expect_ok(conn, "未判定の行: result_code・recorded_by・recorded_atが全部空で作れる",
              "INSERT INTO case_results (case_id) VALUES ('FK-1')")
    expect_error(conn, "制約: result_codeだけ入り、recorded_by・recorded_atが空", errors.CheckViolation,
                 "INSERT INTO case_results (case_id, result_code) VALUES ('FK-2', 'x')")
    expect_error(conn, "制約: recorded_by・recorded_atだけ入り、result_codeが空", errors.CheckViolation,
                 "INSERT INTO case_results (case_id, recorded_by, recorded_at) VALUES ('FK-2', 'OP-1', now())")
    expect_error(conn, "制約: result_codeが空白だけ", errors.CheckViolation, ins_res, ("FK-2", "  ", None, None))
    expect_error(conn, "制約: rule_idだけ指定（rule_revisionがNULL）", errors.CheckViolation,
                 ins_res, ("FK-2", "demo_result", "DEMO-RULE-1", None))
    expect_ok(conn, "採用ルールなしで任意のresult_codeを保存できる", ins_res, ("FK-2", "any_manual_code", None, None))
    expect_ok(conn, "採用ルールの結果（demo_result）と違うresult_codeでも保存できる（参考の記録）",
              ins_res, ("FK-3", "other_result", "DEMO-RULE-1", 1))
    expect_ok(conn, "存在しない採用ルールの組も保存できる（外部キーを持たない）",
              ins_res, ("FK-4", "demo_result", "DEMO-RULE-1", 99))

    # case_checks: 親の結果と、案件の設問への外部キー、制約
    copy_questions(conn, "FK-5")  # 設問はあるが判定結果の行がない案件
    ins_chk = "INSERT INTO case_checks (case_id, question_code, answer) VALUES (%s, %s, %s)"
    expect_error(conn, "外部キー: 結果の行のない案件の回答", errors.ForeignKeyViolation, ins_chk, ("FK-5", "A03", "yes"))
    expect_error(conn, "外部キー: 案件に出していない設問", errors.ForeignKeyViolation, ins_chk, ("CASE-A", "ZZ9", "yes"))
    expect_error(conn, "制約: answerが未定義値", errors.CheckViolation, ins_chk, ("CASE-A", "A03", "maybe"))
    expect_error(conn, "制約: answerがNULL", errors.NotNullViolation, ins_chk, ("CASE-A", "A03", None))
    expect_error(conn, "制約: 同じ案件・設問の回答が重複", errors.UniqueViolation, ins_chk, ("CASE-A", "A03", "yes"))
    ins_auto = ("INSERT INTO case_checks (case_id, question_code, answer, automated_answer) "
                "VALUES ('CASE-A', 'A05', 'pending', %s)")
    expect_error(conn, "制約: automated_answerがyes・no以外", errors.CheckViolation, ins_auto, ("pending",))
    expect_ok(conn, "automated_answerは空でも保存できる", ins_auto, (None,))

    # 受付: 案件を先に保存し、取得を後にする
    intake_case(conn, "IN-OK", lambda: "yes")
    res_row = conn.execute("SELECT result_code, recorded_by, recorded_at FROM case_results WHERE case_id='IN-OK'").fetchone()
    n_q = conn.execute("SELECT count(*) FROM case_questions WHERE case_id='IN-OK'").fetchone()[0]
    n_m = conn.execute("SELECT count(*) FROM review_questions").fetchone()[0]
    record("受付（取得成功）: 全設問のコピー、未判定の結果、A09はanswer・automated_answerともyes",
           n_q == n_m and res_row == (None, None, None)
           and load_state(conn, "IN-OK")[1] == [("A09", "yes", "yes")], f"設問{n_q}行")
    intake_case(conn, "IN-FAIL", timeout_fetch)
    record("受付（取得失敗）: A09はpending、automated_answerは空。案件は作られている",
           load_state(conn, "IN-FAIL")[1] == [("A09", "pending", None)])
    intake_case(conn, "IN-BAD", lambda: "maybe")
    record("受付（2回目の保存が失敗）: A09はpendingのまま、案件と未判定の結果は残る",
           load_state(conn, "IN-BAD")[1] == [("A09", "pending", None)]
           and load_state(conn, "IN-BAD")[0] == (None, None, None))

    # 受付時のコピー
    intake_case(conn, "SAVE-1", lambda: "yes", '{"user_id":"9"}')
    got = conn.execute("SELECT revision, prompt FROM case_questions WHERE case_id='SAVE-1' AND question_code='A03'").fetchone()
    master = conn.execute("SELECT revision, prompt FROM review_questions WHERE question_code='A03'").fetchone()
    record("受付時のコピー: マスターの現在の版（A03はrevision 2）が案件に入る", got == master and got[0] == 2, f"revision {got[0]}")
    old = conn.execute("SELECT revision, prompt FROM case_questions WHERE case_id='CASE-A' AND question_code='A03'").fetchone()
    record("コピーの維持: 先に受け付けた案件はrevision 1の当時の設問文のまま", old[0] == 1 and old[1] != master[1], f"revision {old[0]}")
    conn.execute("UPDATE review_questions SET prompt = prompt || '（さらに改訂）', revision = revision + 1 WHERE question_code='A03'")
    still = conn.execute("SELECT revision, prompt FROM case_questions WHERE case_id='SAVE-1' AND question_code='A03'").fetchone()
    record("マスターを更新しても、受付済み案件の設問は変わらない", still == got)

    # 保存・再表示・上書き・ロールバック
    save(conn, "SAVE-1", "demo_manual_result", None, {"A03": "yes", "A09": "no"})
    res, checks = load_state(conn, "SAVE-1")
    record("保存→再表示: 結果と回答が戻り、受付時の自動回答（automated_answer）は残る",
           res == ("demo_manual_result", None, None)
           and checks == [("A03", "yes", None), ("A09", "no", "yes")])
    row = conn.execute("SELECT recorded_by, recorded_at FROM case_results WHERE case_id='SAVE-1'").fetchone()
    record("保存: 登録者と登録日時が入り、判定済み（result_codeあり）になる", row[0] == "OP-1" and row[1] is not None)

    save(conn, "SAVE-1", "demo_result", ("DEMO-RULE-1", 1), {"A09": "yes"})
    res, checks = load_state(conn, "SAVE-1")
    n_res = conn.execute("SELECT count(*) FROM case_results WHERE case_id='SAVE-1'").fetchone()[0]
    record("再保存は上書き: 最新のみ保持（結果・採用ルールの置換、結果の行は1件）、回答の行は削除されない",
           res == ("demo_result", "DEMO-RULE-1", 1) and n_res == 1
           and checks == [("A03", "yes", None), ("A09", "yes", "yes")])

    save(conn, "SAVE-1", "caution", ("DEMO-RULE-1", 1), {})
    res, checks = load_state(conn, "SAVE-1")
    record("採用ルールの結果と違う結果を選んで保存でき、回答0件の保存でも結果が入る",
           res == ("caution", "DEMO-RULE-1", 1)
           and checks == [("A03", "yes", None), ("A09", "yes", "yes")])

    save(conn, "SAVE-1", "no_rule_result", None, {})
    record("採用ルールを空に戻して保存できる", load_state(conn, "SAVE-1")[0] == ("no_rule_result", None, None))

    record("別接続から再表示: 確認待ち（pending）とautomated_answerなしの行が戻る",
           (lambda: (intake_case(conn, "SAVE-2", timeout_fetch), load_checks_other_connection("SAVE-2"))[1])()
           == [("A09", "pending", None)])

    save(conn, "SAVE-1", "keep_me", None, {"A03": "yes", "A09": "no"})
    before = load_state(conn, "SAVE-1")
    try:
        save(conn, "SAVE-1", "must_not_persist", None, {"A03": "no", "ZZ9": "yes"})
        record("ロールバック: 子の失敗で親子とも元に戻る", False, "エラーにならなかった")
    except errors.ForeignKeyViolation:
        record("ロールバック: 子の失敗で親子とも元に戻る", load_state(conn, "SAVE-1") == before)

    # 振り返り: 判定日時以前に作成された最新のルール版を引く
    def rules_as_of(ts):
        return conn.execute(
            """SELECT rule_id, revision FROM (SELECT DISTINCT ON (rule_id) rule_id, revision
                 FROM decision_rules WHERE created_at <= %s ORDER BY rule_id, revision DESC) r""",
            (ts,),
        ).fetchall()
    record("振り返り: 2026-10-20時点のルールはDEMO-RULE-1のrevision 1",
           rules_as_of("2026-10-20T13:05:00+09:00") == [("DEMO-RULE-1", 1)])
    record("振り返り: 改訂後（2026-10-27以降）はrevision 2を引く",
           rules_as_of("2026-10-28T00:00:00+09:00") == [("DEMO-RULE-1", 2)])

    total = len(results)
    failed = [r for r in results if not r[1]]
    print(f"\n結果: {total - len(failed)}/{total} 件成功。" + ("DB_SCHEMAを使った実行。" if SCHEMA else "ローカルPostgreSQLのみ。Aurora DSQLの動作は未確認。"), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
