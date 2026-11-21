"""py-sql-dynamic-string の検査用の架空コード。実行しない（Semgrepが読むだけ）。

`# ruleid: <ルールID>` は、次の行で検出されること、`# ok: <ルールID>` は、次の行で検出されないことを示す。
"""

from psycopg import sql


def violations(conn, cur, engine, case_id, table):
    # ruleid: py-sql-dynamic-string
    cur.execute(f"SELECT * FROM cases WHERE case_id = '{case_id}'")
    # ruleid: py-sql-dynamic-string
    cur.execute("SELECT * FROM cases WHERE case_id = '%s'" % case_id)
    # ruleid: py-sql-dynamic-string
    cur.execute("SELECT * FROM cases WHERE case_id = '{}'".format(case_id))
    # ruleid: py-sql-dynamic-string
    cur.execute("SELECT * FROM cases WHERE case_id = '" + case_id + "'")
    query = f"DELETE FROM {table}"
    # ruleid: py-sql-dynamic-string
    conn.execute(query)
    # ruleid: py-sql-dynamic-string
    cur.executemany(f"INSERT INTO {table} VALUES (%s)", [(1,)])
    # ruleid: py-sql-dynamic-string
    engine.exec_driver_sql(f"DROP TABLE {table}")
    # ruleid: py-sql-dynamic-string
    text(f"SELECT 1 FROM {table}")
    percent_query = "DELETE FROM %s" % table
    # ruleid: py-sql-dynamic-string
    cur.execute(percent_query)
    format_query = "DELETE FROM {}".format(table)
    # ruleid: py-sql-dynamic-string
    cur.execute(format_query)
    text_query = f"SELECT 1 FROM {table}"
    # ruleid: py-sql-dynamic-string
    text(text_query)


def controls(conn, cur, case_id, table):
    # ok: py-sql-dynamic-string
    cur.execute("SELECT * FROM cases WHERE case_id = %s", (case_id,))
    # ok: py-sql-dynamic-string
    cur.execute("SELECT 1")
    # ok: py-sql-dynamic-string
    cur.execute("SELECT * FROM cases " + "WHERE closed = false")
    # ok: py-sql-dynamic-string
    cur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))
    message = f"case {case_id}"
    # ok: py-sql-dynamic-string
    conn.execute("SELECT %s", (message,))
    fixed_query = "SELECT * FROM cases WHERE closed = false"
    # ok: py-sql-dynamic-string
    cur.execute(fixed_query)
