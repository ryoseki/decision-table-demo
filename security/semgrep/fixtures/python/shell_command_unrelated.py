"""py-shell-command-injection の検査用の架空コード（自前の run・system）。
実行しない（Semgrepが読むだけ）。

subprocess・os から import していない同名の関数は、検出しない。
このファイルに、subprocess・os の import を足さない。import の範囲が、この例に影響するため、別のファイルに書く。
"""


def run(command, shell=False):
    return command


def system(command):
    return command


class Tool:
    def run(self, command, shell=False):
        return command


def controls(user_value):
    # ok: py-shell-command-injection
    run(f"ls {user_value}", shell=True)
    # ok: py-shell-command-injection
    system(user_value)
    # ok: py-shell-command-injection
    Tool().run(f"ls {user_value}", shell=True)
