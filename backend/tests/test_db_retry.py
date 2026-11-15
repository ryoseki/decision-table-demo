"""OCC（SQLSTATE 40001）の再試行と、Aurora DSQLのIAMトークン接続の検証（DBなし）。

偽の保存先・偽のboto3・偽のpsycopg接続だけを使う。実Aurora DSQLの結果ではない。
再試行の方針（4回まで、待ち時間は50・100・200ms、上限500ms、ジッター付き、
40001だけ再試行、使い切ったらStoreBusy）は決定済み。
"""

import importlib
import os
import sys
import types
import unittest
from unittest import mock

import psycopg.errors

from app import db_retry, db_store
from app.domain import CaseNotFound, StoreBusy


class Flaky:
    """最初のfailures回だけ、指定の例外を投げる。"""

    def __init__(self, failures: int, error: Exception | None = None) -> None:
        self.failures = failures
        self.calls = 0
        self.error = error or psycopg.errors.SerializationFailure("OC000（検証用）")

    @db_retry.with_occ_retry
    def run(self, value: str) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return value


class RetryPolicy(unittest.TestCase):
    def setUp(self):
        self.waits: list[float] = []
        patches = (
            mock.patch.object(db_retry, "sleep", self.waits.append),
            mock.patch.object(db_retry, "jitter", lambda: 1.0),  # 上限いっぱい
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_success_without_conflict_does_not_wait(self):
        flaky = Flaky(0)
        self.assertEqual(flaky.run("ok"), "ok")
        self.assertEqual((flaky.calls, self.waits), (1, []))

    def test_retries_then_succeeds_with_doubling_waits(self):
        flaky = Flaky(3)
        self.assertEqual(flaky.run("ok"), "ok")
        self.assertEqual(flaky.calls, 4)
        self.assertEqual(self.waits, [0.05, 0.1, 0.2])

    def test_gives_up_after_four_attempts_with_store_busy(self):
        flaky = Flaky(99)
        with self.assertRaises(StoreBusy) as caught:
            flaky.run("ok")
        self.assertEqual(flaky.calls, 4, "最初の1回＋再試行3回")
        self.assertEqual(len(self.waits), 3, "最後の失敗のあとは待たない")
        self.assertIsInstance(
            caught.exception.__cause__, psycopg.errors.SerializationFailure
        )

    def test_wait_is_capped_at_500ms(self):
        self.assertEqual(db_retry.delay_ceiling(1), 0.05)
        self.assertEqual(db_retry.delay_ceiling(3), 0.2)
        self.assertEqual(db_retry.delay_ceiling(4), 0.4)
        self.assertEqual(db_retry.delay_ceiling(5), 0.5)
        self.assertEqual(db_retry.delay_ceiling(9), 0.5)

    def test_jitter_keeps_wait_between_half_and_full_ceiling(self):
        for fraction, expected in ((0.0, 0.025), (0.5, 0.0375), (1.0, 0.05)):
            self.waits.clear()
            with mock.patch.object(db_retry, "jitter", lambda f=fraction: f):
                Flaky(1).run("ok")
            self.assertAlmostEqual(self.waits[0], expected)

    def test_other_errors_are_not_retried(self):
        for error in (
            RuntimeError("別の失敗"),
            CaseNotFound("CASE-X"),
            psycopg.errors.DeadlockDetected("40P01は対象外"),
            psycopg.errors.UniqueViolation("23505は対象外"),
        ):
            flaky = Flaky(99, error)
            with self.assertRaises(type(error)):
                flaky.run("ok")
            self.assertEqual((flaky.calls, self.waits), (1, []))

    def test_other_error_after_conflict_stops_retrying(self):
        calls = []

        @db_retry.with_occ_retry
        def run():
            calls.append(1)
            if len(calls) == 1:
                raise psycopg.errors.SerializationFailure("OC000（検証用）")
            raise RuntimeError("別の失敗")

        with self.assertRaises(RuntimeError):
            run()
        self.assertEqual((len(calls), len(self.waits)), (2, 1))

    def test_store_methods_are_wrapped(self):
        for name in ("create_case", "set_auto_answer", "save_judgment"):
            method = getattr(db_store.PostgresCaseStore, name)
            self.assertTrue(hasattr(method, "__wrapped__"), name)
        for name in ("get_case", "list_cases", "list_rules"):
            method = getattr(db_store.PostgresCaseStore, name)
            self.assertFalse(hasattr(method, "__wrapped__"), f"{name}は読み取り")


class DsqlConnection(unittest.TestCase):
    """DSQL_ENDPOINT があるときだけトークン接続にする。トークンは出力に残さない。"""

    ENDPOINT = "example.dsql.ap-northeast-1.on.aws"  # 架空

    def connect(self, env: dict[str, str]):
        client = mock.Mock()
        client.generate_db_connect_admin_auth_token.return_value = "ADMIN-TOKEN"
        client.generate_db_connect_auth_token.return_value = "ROLE-TOKEN"
        fake_boto3 = types.SimpleNamespace(client=mock.Mock(return_value=client))
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.dict(sys.modules, {"boto3": fake_boto3}),
            mock.patch.object(db_store.psycopg, "connect") as connect,
        ):
            db_store.connect_from_env()
        return connect.call_args.kwargs, client, fake_boto3

    def test_admin_user_uses_admin_token(self):
        kwargs, client, boto3 = self.connect(
            {
                "DSQL_ENDPOINT": self.ENDPOINT,
                "AWS_REGION": "ap-northeast-1",
                "DB_USER": "admin",
            }
        )
        boto3.client.assert_called_once_with("dsql", region_name="ap-northeast-1")
        client.generate_db_connect_admin_auth_token.assert_called_once_with(
            self.ENDPOINT, "ap-northeast-1"
        )
        client.generate_db_connect_auth_token.assert_not_called()
        self.assertEqual(kwargs["password"], "ADMIN-TOKEN")
        self.assertEqual(
            (kwargs["host"], kwargs["dbname"], kwargs["sslmode"], kwargs["user"]),
            (self.ENDPOINT, "postgres", "require", "admin"),
        )

    def test_custom_role_uses_role_token_and_schema(self):
        kwargs, client, _ = self.connect(
            {
                "DSQL_ENDPOINT": self.ENDPOINT,
                "DSQL_REGION": "us-east-1",
                "AWS_REGION": "ap-northeast-1",
                "DB_USER": "app_role",
                "DB_SCHEMA": "demo",
            }
        )
        client.generate_db_connect_auth_token.assert_called_once_with(
            self.ENDPOINT, "us-east-1"
        )
        client.generate_db_connect_admin_auth_token.assert_not_called()
        self.assertEqual(kwargs["password"], "ROLE-TOKEN")
        self.assertEqual(kwargs["options"], "-c search_path=demo")

    def test_each_connection_makes_a_new_token(self):
        client = mock.Mock()
        client.generate_db_connect_auth_token.side_effect = ["T1", "T2"]
        fake_boto3 = types.SimpleNamespace(client=mock.Mock(return_value=client))
        env = {"DSQL_ENDPOINT": self.ENDPOINT, "AWS_REGION": "r", "DB_USER": "u"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.dict(sys.modules, {"boto3": fake_boto3}),
            mock.patch.object(db_store.psycopg, "connect") as connect,
        ):
            db_store.connect_from_env()
            db_store.connect_from_env()
        self.assertEqual(
            [call.kwargs["password"] for call in connect.call_args_list], ["T1", "T2"]
        )

    def test_real_boto3_makes_distinct_tokens_offline(self):
        """偽のboto3が実物の形とずれていないことを、実boto3（通信なしの署名）で確認する。"""
        env = {
            "DSQL_ENDPOINT": self.ENDPOINT,
            "AWS_REGION": "ap-northeast-1",
            "AWS_ACCESS_KEY_ID": "AKIAEXAMPLEDUMMY0000",  # 架空
            "AWS_SECRET_ACCESS_KEY": "dummy-secret-for-offline-signing",  # 架空
        }
        tokens = {}
        for user in ("admin", "app_role"):
            with (
                mock.patch.dict(os.environ, env | {"DB_USER": user}, clear=True),
                mock.patch.object(db_store.psycopg, "connect") as connect,
            ):
                db_store.connect_from_env()
            tokens[user] = connect.call_args.kwargs["password"]
        for token in tokens.values():
            self.assertIsInstance(token, str)
            self.assertIn(self.ENDPOINT, token)
        self.assertNotEqual(tokens["admin"], tokens["app_role"])

    def test_missing_settings_fail_instead_of_connecting_blindly(self):
        for env in (
            {"DSQL_ENDPOINT": self.ENDPOINT, "AWS_REGION": "r"},  # DB_USERなし
            {"DSQL_ENDPOINT": self.ENDPOINT, "DB_USER": "u"},  # リージョンなし
        ):
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.dict(sys.modules, {"boto3": mock.Mock()}),
                mock.patch.object(db_store.psycopg, "connect") as connect,
                self.assertRaises(KeyError),
            ):
                db_store.connect_from_env()
            connect.assert_not_called()

    def test_endpoint_wins_over_local_host_when_both_are_set(self):
        kwargs, _, _ = self.connect(
            {
                "DSQL_ENDPOINT": self.ENDPOINT,
                "AWS_REGION": "r",
                "DB_USER": "u",
                "DB_HOST": "db",
                "DB_PASSWORD": "local",
            }
        )
        self.assertEqual(
            (kwargs["host"], kwargs["password"]), (self.ENDPOINT, "ROLE-TOKEN")
        )

    def test_local_connection_uses_repeatable_read_like_dsql(self):
        env = {"DB_HOST": "db", "DB_NAME": "n", "DB_USER": "u", "DB_PASSWORD": "p"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(db_store.psycopg, "connect") as connect,
        ):
            conn = db_store.connect_from_env()
        self.assertIs(conn, connect.return_value)
        self.assertEqual(
            conn.isolation_level, db_store.psycopg.IsolationLevel.REPEATABLE_READ
        )

    def test_without_endpoint_uses_local_password_and_never_imports_boto3(self):
        env = {"DB_HOST": "db", "DB_NAME": "n", "DB_USER": "u", "DB_PASSWORD": "p"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.dict(sys.modules, {"boto3": None}),  # import すると失敗する
            mock.patch.object(db_store.psycopg, "connect") as connect,
        ):
            db_store.connect_from_env()
        kwargs = connect.call_args.kwargs
        self.assertEqual((kwargs["host"], kwargs["password"]), ("db", "p"))
        self.assertEqual(kwargs["sslmode"], "disable")


class CommitTimeConflict(unittest.TestCase):
    """DSQLのOCCはコミット時に判定される。コミット時の40001も再試行する。"""

    class _Conn:
        def __init__(self, fail_on_exit: bool, log: list[str]):
            self._fail, self._log = fail_on_exit, log

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            if self._fail:
                self._log.append("commit-conflict")
                raise psycopg.errors.SerializationFailure("OC000（検証用）")
            self._log.append("commit")

        def execute(self, sql, *args):
            self._log.append("execute")
            return types.SimpleNamespace(rowcount=1)

    def test_conflict_at_commit_is_retried(self):
        log: list[str] = []
        connections = iter([True, False])
        store = db_store.PostgresCaseStore(lambda: self._Conn(next(connections), log))
        with mock.patch.object(db_retry, "sleep", lambda _: None):
            store.set_auto_answer("CASE-X", "A09", "yes")
        self.assertEqual(log, ["execute", "commit-conflict", "execute", "commit"])

    def test_conflict_at_commit_every_time_becomes_store_busy(self):
        log: list[str] = []
        store = db_store.PostgresCaseStore(lambda: self._Conn(True, log))
        with (
            mock.patch.object(db_retry, "sleep", lambda _: None),
            self.assertRaises(StoreBusy),
        ):
            store.set_auto_answer("CASE-X", "A09", "yes")
        self.assertEqual(log.count("commit-conflict"), 4)


class MainSelectsStore(unittest.TestCase):
    """DB_HOSTかDSQL_ENDPOINTがあればDB、どちらもなければメモリの保存先。"""

    def selected(self, env: dict[str, str]):
        from app import main

        with mock.patch.dict(os.environ, env, clear=True):
            importlib.reload(main)
            store = type(main.app.state.case_store)
        self.addCleanup(importlib.reload, main)
        return store

    def test_selection(self):
        from app import intake

        self.assertIs(
            self.selected({"DSQL_ENDPOINT": "x.example"}), db_store.PostgresCaseStore
        )
        self.assertIs(self.selected({"DB_HOST": "db"}), db_store.PostgresCaseStore)
        self.assertIs(self.selected({}), intake.InMemoryCaseStore)


if __name__ == "__main__":
    unittest.main()
