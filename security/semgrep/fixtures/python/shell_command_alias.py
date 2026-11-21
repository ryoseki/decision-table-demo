"""py-shell-command-injection の検査用の架空コード（別名と、名前を指定した import）。
実行しない（Semgrepが読むだけ）。

現在のSemgrep（固定した版）は、import で別名を付けても、`from ... import ...` で名前を指定しても、検出する。
import の範囲が、ほかの例に影響しないよう、このファイルには、検出する例だけを書く。
"""

import os as o
import subprocess as sp
from os import system
from subprocess import run as r


def violations(user_value):
    # ruleid: py-shell-command-injection
    sp.run(f"ls {user_value}", shell=True)
    # ruleid: py-shell-command-injection
    r("ls " + user_value, shell=True)
    # ruleid: py-shell-command-injection
    system(user_value)
    # ruleid: py-shell-command-injection
    o.popen(f"echo {user_value}")
