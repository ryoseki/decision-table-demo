"""py-eval-exec-dynamic の検査用の架空コード。実行しない（Semgrepが読むだけ）。"""

import ast


def violations(user_value):
    # ruleid: py-eval-exec-dynamic
    eval(user_value)
    # ruleid: py-eval-exec-dynamic
    exec(user_value, {})
    # ruleid: py-eval-exec-dynamic
    eval("1 + " + user_value)


def controls(user_value):
    # ok: py-eval-exec-dynamic
    ast.literal_eval(user_value)
    # ok: py-eval-exec-dynamic
    eval("1 + 1")
