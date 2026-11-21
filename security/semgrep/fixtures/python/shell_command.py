"""py-shell-command-injection の検査用の架空コード。実行しない（Semgrepが読むだけ）。"""

import os
import subprocess


def violations(user_value):
    # ruleid: py-shell-command-injection
    subprocess.run(f"ls {user_value}", shell=True)
    # ruleid: py-shell-command-injection
    subprocess.Popen("ls " + user_value, shell=True)
    # ruleid: py-shell-command-injection
    subprocess.check_output(user_value, shell=True, text=True)
    # ruleid: py-shell-command-injection
    os.system("echo " + user_value)
    # ruleid: py-shell-command-injection
    os.popen(f"echo {user_value}")
    # ruleid: py-shell-command-injection
    subprocess.call("ls " + user_value, shell=True)
    # ruleid: py-shell-command-injection
    subprocess.check_call(f"ls {user_value}", shell=True)


def controls(user_value):
    # ok: py-shell-command-injection
    subprocess.run(["ls", user_value], check=True)
    # ok: py-shell-command-injection
    subprocess.run("ls -l", shell=True, check=True)
    # ok: py-shell-command-injection
    os.system("true")
    # ok: py-shell-command-injection
    subprocess.call("ls -l", shell=True)
    # ok: py-shell-command-injection
    subprocess.check_call(["ls", user_value])
