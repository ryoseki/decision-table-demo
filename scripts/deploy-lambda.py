#!/usr/bin/env python3
"""承認済みのLambda ZIPを、既存のエイリアス demo へ配布する（標準ライブラリのみ）。

AWS CLI（`aws lambda ...`）をsubprocessで呼ぶ。初回構築・IAM・関数URL・同時実行数の操作はしない
（infra/cd/bootstrap.md の人の操作）。変更の前にすべて検証し（fail closed）、変更後は
スモーク確認に失敗したら、こちらが切り替えたエイリアスだけを記録した直前の正常版へ戻す。
結果は常に --result-file のJSONへ書く。Lambdaの設定全体や資格情報は出力・保存しない。
プロセスが強制終了・タイムアウトした場合は戻し処理が走らないことがある。その場合は結果JSONの
recoveryNote と infra/cd/bootstrap.md の手順で人が確認する。
エイリアスが既に同じソースSHA・ZIPの版を指し、既存のスモーク確認が合格した場合は、変更しない
成功（overall=no-change-success）として終え、新しい配布の成功とは扱わない。
`--verify-artifact-only` は成果物の検証だけを行い、AWS CLI・資格情報・通信を使わない。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

REGION = "ap-northeast-1"
ALIAS = "demo"
RUNTIME = "python3.14"
ARCHITECTURE = "x86_64"
HANDLER = "run.sh"
EXEC_WRAPPER = "/opt/bootstrap"
ADAPTER_PORT = "8000"
# レイヤーはこの固定ARNだけを許す（マニフェストが改ざんされても別のレイヤーにしない）。
PINNED_LAYER_ARN = "arn:aws:lambda:ap-northeast-1:753240598075:layer:LambdaAdapterLayerX86:28"

SHA_RE = re.compile(r"[0-9a-f]{40}")
HEX64_RE = re.compile(r"[0-9a-f]{64}")
CODE_SHA_RE = re.compile(r"[A-Za-z0-9+/]{43}=")
VERSION_RE = re.compile(r"[1-9][0-9]{0,17}")
RUN_RE = re.compile(r"[0-9]{1,20}")
FUNCTION_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
BASE_URL_RE = re.compile(r"https://[a-z0-9]{8,64}\.lambda-url\.ap-northeast-1\.on\.aws")
DESCRIPTION_RE = re.compile(
    r"source=(?P<source>[0-9a-f]{40});zip=(?P<zip>[0-9a-f]{64});run=(?P<run>[0-9]{1,20})"
)

AwsRunner = Callable[[list[str]], dict]
SmokeRunner = Callable[[str, str, str], bool]


class DeployError(Exception):
    """検証・操作の失敗。メッセージは秘密を含まないよう作る。"""


class AwsError(DeployError):
    def __init__(self, operation: str, returncode: int, conflict: bool = False):
        super().__init__(f"AWS操作に失敗: {operation} (終了コード {returncode})")
        self.operation = operation
        self.conflict = conflict


class Interrupted(Exception):
    pass


def make_description(source_sha: str, zip_hex: str, run_id: str) -> str:
    return f"source={source_sha};zip={zip_hex};run={run_id}"


def parse_description(text: object) -> dict[str, str] | None:
    if not isinstance(text, str):
        return None
    match = DESCRIPTION_RE.fullmatch(text)
    return match.groupdict() if match else None


def config_problems(config: dict) -> list[str]:
    """検査対象の設定（関数・版の共通）。環境変数の値は保持せず、問題の名前だけ返す。"""
    variables = (config.get("Environment") or {}).get("Variables") or {}
    layers = [layer.get("Arn") for layer in config.get("Layers") or []]
    problems = []
    if config.get("Runtime") != RUNTIME:
        problems.append("runtime")
    if config.get("Architectures") != [ARCHITECTURE]:
        problems.append("architecture")
    if config.get("Handler") != HANDLER:
        problems.append("handler")
    if layers != [PINNED_LAYER_ARN]:
        problems.append("layers")
    if variables.get("AWS_LAMBDA_EXEC_WRAPPER") != EXEC_WRAPPER:
        problems.append("execWrapper")
    if variables.get("PORT") != ADAPTER_PORT:
        problems.append("port")
    if config.get("State") not in (None, "Active"):
        problems.append("state")
    if config.get("LastUpdateStatus") not in (None, "Successful"):
        problems.append("lastUpdateStatus")
    return problems


def code_sha_from_hex(zip_hex: str) -> str:
    return base64.b64encode(bytes.fromhex(zip_hex)).decode("ascii")


def sha256_file(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.digest()


def load_manifest(artifact_dir: Path, source_sha: str, zip_hex: str) -> tuple[Path, dict]:
    """成果物のmanifestとZIPを検証する。ビルド結果の出力（期待値）とも照合する。"""
    zip_name = f"lambda-{source_sha}.zip"
    if not artifact_dir.is_dir():
        raise DeployError("成果物ディレクトリがありません")
    names = {entry.name for entry in artifact_dir.iterdir()}
    if names != {"manifest.json", zip_name}:
        raise DeployError("成果物の構成が想定と異なります")
    manifest_path = artifact_dir / "manifest.json"
    zip_path = artifact_dir / zip_name
    for path in (manifest_path, zip_path):
        if path.is_symlink() or not path.is_file():
            raise DeployError("成果物は通常のファイルである必要があります")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DeployError("manifest.json を読み込めません") from error
    if not isinstance(manifest, dict):
        raise DeployError("manifest.json の形式が不正です")
    zip_info = manifest.get("zip")
    adapter = manifest.get("adapter")
    if not isinstance(zip_info, dict) or not isinstance(adapter, dict):
        raise DeployError("manifest.json の zip/adapter が不正です")

    digest = sha256_file(zip_path)
    checks = {
        "schema": manifest.get("schema") == 1,
        "sourceSHA": manifest.get("sourceSHA") == source_sha,
        "dirty": manifest.get("dirty") is False,
        "releaseEligible": manifest.get("releaseEligible") is True,
        "runtime": manifest.get("runtime") == RUNTIME,
        "architecture": manifest.get("architecture") == ARCHITECTURE,
        "layerArn": adapter.get("layerArn") == PINNED_LAYER_ARN,
        "execWrapper": adapter.get("execWrapper") == EXEC_WRAPPER,
        "handler": adapter.get("handler") == HANDLER,
        "port": adapter.get("port") == int(ADAPTER_PORT),
        "zipFile": zip_info.get("file") == zip_name,
        "zipBytes": zip_info.get("bytes") == zip_path.stat().st_size,
        "zipHex": zip_info.get("sha256Hex") == digest.hex() == zip_hex,
        "zipCodeSha": zip_info.get("codeSha256") == base64.b64encode(digest).decode("ascii"),
    }
    failed = sorted(name for name, ok in checks.items() if not ok)
    if failed:
        raise DeployError("manifest検証に失敗: " + ",".join(failed))
    return zip_path, manifest


class Result:
    """結果JSON。段階ごとにアトミックに書き直す（強制終了でも直前までの記録が残る）。"""

    def __init__(self, path: Path, data: dict):
        self.path = path
        self.data = data

    def update(self, **fields: object) -> None:
        self.data.update(fields)
        self.data["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.flush()

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".result-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(self.data, indent=2, sort_keys=True) + "\n")
            os.replace(temp, self.path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise


class Deployer:
    def __init__(
        self,
        *,
        artifact_dir: Path,
        source_sha: str,
        zip_hex: str,
        ci_run_url: str,
        run_id: str,
        run_attempt: str,
        function_name: str,
        base_url: str,
        layer_code_sha256: str,
        result_file: Path,
        aws: AwsRunner,
        smoke: SmokeRunner,
    ):
        self.artifact_dir = artifact_dir
        self.source_sha = source_sha
        self.zip_hex = zip_hex
        self.run_id = run_id
        self.run_attempt = run_attempt
        self.function = function_name
        self.base_url = base_url
        self.layer_code_sha256 = layer_code_sha256
        self.aws = aws
        self.smoke = smoke
        self.code_sha = code_sha_from_hex(zip_hex)
        self.prev: dict | None = None  # version/source/zip/aliasRevision
        self.new_version: str | None = None
        self.alias_rev: str | None = None  # こちらの最新の更新が返したRevisionId
        self.switched = False
        self.function_revision: str | None = None  # 検証時の設定のRevisionId
        self.already_current = False
        self.result = Result(
            result_file,
            {
                "schema": 1,
                "sourceSHA": source_sha,
                "zipSha256": zip_hex,
                "ciRunUrl": ci_run_url,
                "runId": run_id,
                "runAttempt": run_attempt,
                "overall": "in-progress",
                "stage": "start",
                "validation": "not-run",
                "deployment": "not-started",
                # AWSの変更操作を始める直前に true にする。成功を意味せず、結果の書き込み自体が
                # 失敗した場合は記録されない（その場合の有無は未確認）。
                "mutationStarted": False,
                "previousSmoke": "not-run",
                "smoke": "not-run",
                "rollback": "not-needed",
                "versions": {"previous": None, "new": None, "reused": False},
                "note": None,
                "alias": {"name": ALIAS, "version": None, "revisionId": None, "state": "untouched"},
                "lastGood": None,
                "error": None,
                "recoveryNote": None,
            },
        )

    # --- AWS呼び出し（結果は必要な項目だけ取り出す） ---
    def _lambda(self, *args: str) -> dict:
        return self.aws(["lambda", *args])

    def _versions(self, new: str | None = None, reused: bool = False) -> dict:
        previous = self.prev["version"] if self.prev else None
        return {"previous": previous, "new": new, "reused": reused}

    def _report_id(self, label: str) -> str:
        return f"run{self.run_id}-{self.run_attempt}-{label}"

    def _alias_state(self, response: dict) -> tuple[str, str]:
        version = response.get("FunctionVersion")
        revision = response.get("RevisionId")
        weights = (response.get("RoutingConfig") or {}).get("AdditionalVersionWeights")
        if weights:
            raise DeployError("エイリアスが加重ルーティングです。人が確認してください")
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            raise DeployError("エイリアスの指す版が数値の版ではありません")
        if not isinstance(revision, str) or not revision:
            raise DeployError("エイリアスのRevisionIdを取得できません")
        return version, revision

    # --- 検証（変更なし） ---
    def validate(self) -> None:
        self.result.update(stage="validate-manifest")
        self.zip_path, self.manifest = load_manifest(
            self.artifact_dir, self.source_sha, self.zip_hex
        )
        self.result.update(stage="validate-aws")

        url = self._lambda(
            "get-function-url-config", "--function-name", self.function, "--qualifier", ALIAS
        )
        function_url = str(url.get("FunctionUrl", "")).rstrip("/")
        if (
            url.get("AuthType") != "NONE"
            or url.get("InvokeMode") != "BUFFERED"
            or function_url != self.base_url
        ):
            raise DeployError("関数URLの設定が期待と一致しません")

        config = self._lambda("get-function-configuration", "--function-name", self.function)
        problems = config_problems(config)
        revision = config.get("RevisionId")
        if not isinstance(revision, str) or not revision:
            problems.append("revisionId")
        del config  # 環境変数を保持しない
        if problems:
            raise DeployError("関数設定が期待と一致しません: " + ",".join(problems))
        # 検証した設定のRevisionId。コード更新はこの版を条件にする（検証後に変わっていたら更新は失敗する）
        self.function_revision = revision

        layer = self._lambda("get-layer-version-by-arn", "--arn", PINNED_LAYER_ARN)
        if (layer.get("Content") or {}).get("CodeSha256") != self.layer_code_sha256:
            raise DeployError("レイヤーのCodeSha256が記録した期待値と一致しません")

        alias = self._lambda("get-alias", "--function-name", self.function, "--name", ALIAS)
        version, alias_revision = self._alias_state(alias)
        old = self._lambda(
            "get-function-configuration", "--function-name", self.function, "--qualifier", version
        )
        meta = parse_description(old.get("Description"))
        if meta is None:
            raise DeployError("直前の版に利用できる出所情報（description）がありません")
        if old.get("CodeSha256") != code_sha_from_hex(meta["zip"]):
            raise DeployError("直前の版のコードと出所情報のハッシュが一致しません")
        self.prev = {
            "version": version,
            "sourceSHA": meta["source"],
            "zipSha256": meta["zip"],
            "aliasRevision": alias_revision,
        }
        self.alias_rev = alias_revision
        self.result.update(
            validation="passed",
            versions=self._versions(),
            alias={
                "name": ALIAS,
                "version": version,
                "revisionId": alias_revision,
                "state": "untouched",
            },
        )

    def check_previous(self) -> None:
        assert self.prev is not None
        self.result.update(stage="previous-smoke")
        ok = self.smoke(self.base_url, self.prev["sourceSHA"], self._report_id("previous"))
        self.result.update(previousSmoke="passed" if ok else "failed")
        if not ok:
            raise DeployError("現在のエイリアスの版が正常ではないため、変更しませんでした")
        self.result.update(
            lastGood={
                "version": self.prev["version"],
                "sourceSHA": self.prev["sourceSHA"],
                "zipSha256": self.prev["zipSha256"],
                "smoke": "passed",
            }
        )

    # --- 変更 ---
    def already_current_check(self) -> bool:
        """直前の版が今回と同じソース・ZIPで、全項目のスモーク確認も合格済みなら、変更しない。"""
        assert self.prev is not None
        if (
            self.prev["sourceSHA"] != self.source_sha
            or self.prev["zipSha256"] != self.zip_hex
            or self.result.data["previousSmoke"] != "passed"
        ):
            return False
        self.already_current = True
        self.result.update(
            stage="done",
            deployment="already-current",
            smoke="not-run",
            versions=self._versions(),
            note=(
                "エイリアスは既に同じソースSHA・ZIPの版を指し、その版の既存スモーク確認は合格した。"
                "変更なしの成功で、新しい版は発行しておらず、新しい配布の成功ではない。"
            ),
        )
        return True

    # --- 変更 ---
    def mutate(self) -> None:
        assert self.prev is not None and self.function_revision is not None
        prev = self.prev
        self.result.update(
            deployment="in-progress",
            recoveryNote=(
                "強制終了・タイムアウトの場合は戻し処理が走らない。エイリアス demo の指す版を確認し、"
                f"直前の正常版（版 {prev['version']}）へ戻すか、infra/cd/bootstrap.md の公開停止を使う。"
            ),
        )
        self.result.update(stage="update-code", mutationStarted=True)
        # 検証時に取得したRevisionIdを条件にする。以後に設定が変わっていれば更新は失敗する
        updated = self._lambda(
            "update-function-code",
            "--function-name",
            self.function,
            "--zip-file",
            f"fileb://{self.zip_path}",
            "--revision-id",
            self.function_revision,
        )
        own_revision = updated.get("RevisionId")
        if updated.get("CodeSha256") != self.code_sha:
            raise DeployError("アップロード後のCodeSha256がmanifestと一致しません")
        if not isinstance(own_revision, str) or not own_revision:
            raise DeployError("コード更新の応答にRevisionIdがありません")

        self.result.update(stage="wait-update")
        self._lambda("wait", "function-updated-v2", "--function-name", self.function)
        current = self._lambda("get-function-configuration", "--function-name", self.function)
        problems = config_problems(current)
        if current.get("CodeSha256") != self.code_sha:
            problems.append("codeSha256")
        # こちらの更新のRevisionIdのまま（他者の変更が入っていない）ことを、発行の条件にする
        if current.get("RevisionId") != own_revision:
            problems.append("revisionId")
        del current
        if problems:
            raise DeployError(
                "コード更新後の関数の状態が期待と一致しません（発行しません）: " + ",".join(problems)
            )

        self.result.update(stage="publish")
        published = self._lambda(
            "publish-version",
            "--function-name",
            self.function,
            "--code-sha256",
            self.code_sha,
            "--revision-id",
            own_revision,
            "--description",
            make_description(self.source_sha, self.zip_hex, self.run_id),
        )
        version = published.get("Version")
        if (
            not isinstance(version, str)
            or not VERSION_RE.fullmatch(version)
            or published.get("CodeSha256") != self.code_sha
        ):
            raise DeployError("発行された版の内容が期待と一致しません")
        # 発行直後の版は Pending のことがある（既存の版を再利用する場合も同じ手順）。Active になるまで
        # 待ってから確認する。待機はAWS CLIの上限つき。失敗・タイムアウトはエイリアスを動かさず止まる。
        self.result.update(stage="wait-published")
        self._lambda(
            "wait",
            "function-active-v2",
            "--function-name",
            self.function,
            "--qualifier",
            version,
        )
        reused = self._verify_published(version)
        self.new_version = version
        self.result.update(versions=self._versions(version, reused))

        self.result.update(stage="switch-alias")
        try:
            switched = self._lambda(
                "update-alias",
                "--function-name",
                self.function,
                "--name",
                ALIAS,
                "--function-version",
                version,
                "--revision-id",
                prev["aliasRevision"],
                "--routing-config",
                '{"AdditionalVersionWeights":{}}',
            )
        except AwsError as error:
            self._unknown_alias_outcome(error)
            raise
        revision = switched.get("RevisionId")
        if (
            switched.get("FunctionVersion") != version
            or (switched.get("RoutingConfig") or {}).get("AdditionalVersionWeights")
            or not isinstance(revision, str)
            or not revision
        ):
            self._unknown_alias_outcome(None)
        self._mark_switched(version, revision)

        self.result.update(stage="new-smoke")
        ok = self.smoke(self.base_url, self.source_sha, self._report_id("new"))
        self.result.update(smoke="passed" if ok else "failed")
        if not ok:
            raise DeployError("新しい版のスモーク確認に失敗しました")
        self.result.update(
            lastGood={
                "version": version,
                "sourceSHA": self.source_sha,
                "zipSha256": self.zip_hex,
                "smoke": "passed",
            }
        )

    def _verify_published(self, version: str) -> bool:
        """発行（または既存の同一内容）の版を読み、今回のソース・ZIP・検査済みの設定と証明できるか確認する。

        AWSは、コードも設定も前回の版から変わらないと新しい版を作らず既存の版を返す。再利用は、
        descriptionのソースSHA・ZIPのハッシュ、コードのハッシュ、検査対象の設定がすべて一致すると
        確認できたときだけ。それ以外は閉じて止まり、手動確認とする（設定の変更で版を作ることはしない）。
        戻り値: 既存の版を再利用したか。
        """
        assert self.prev is not None
        manual = "。手動で確認してください（infra/cd/bootstrap.md の「発行済みの版が既にある場合」）"
        if version == self.prev["version"]:
            raise DeployError("発行された版がエイリアスの現在の版と同じです" + manual)
        config = self._lambda(
            "get-function-configuration", "--function-name", self.function, "--qualifier", version
        )
        meta = parse_description(config.get("Description"))
        problems = config_problems(config)
        if config.get("CodeSha256") != self.code_sha:
            problems.append("codeSha256")
        del config
        if meta is None or meta["source"] != self.source_sha or meta["zip"] != self.zip_hex:
            problems.append("description")
        if problems:
            raise DeployError(
                f"発行された版 {version} を今回のソース・設定と証明できません: "
                + ",".join(problems)
                + manual
            )
        assert meta is not None
        return meta["run"] != self.run_id

    def _mark_switched(self, version: str, revision: str) -> None:
        self.switched = True
        self.alias_rev = revision
        self.result.update(
            deployment="completed",
            alias={"name": ALIAS, "version": version, "revisionId": revision, "state": "switched"},
        )

    def _unknown_alias_outcome(self, error: AwsError | None) -> None:
        """エイリアス更新の結果を、こちらの成功応答で確認できないとき。常に例外で終える。

        読み直して得たRevisionIdは、外部の変更かもしれず、戻しの根拠にしない（自動では戻さず、
        新しい版を指していても上書きしない）。観測した状態は記録だけして、人が確認する。
        """
        assert self.prev is not None
        if error is not None and error.conflict:
            # 条件付き更新が拒否された。こちらは切り替えていないが、実際の版は不明。
            self.result.update(
                alias={
                    "name": ALIAS,
                    "version": None,
                    "revisionId": None,
                    "state": "conflict-not-switched",
                },
                recoveryNote=(
                    "エイリアスが外部で変更されたため切り替えなかった。エイリアスの現在の版は不明"
                    f"（確認前に直前の版 {self.prev['version']} とは仮定しない）。"
                    f"発行済みの版 {self.new_version} は残す。戻し処理はしない。人が確認する。"
                ),
            )
            raise DeployError("エイリアスが外部で変更されたため切り替えませんでした")
        observed = {"version": None, "revisionId": None}
        try:
            current = self._lambda("get-alias", "--function-name", self.function, "--name", ALIAS)
            version, revision = self._alias_state(current)
            observed = {"version": version, "revisionId": revision}
        except DeployError:
            pass
        self.result.update(
            deployment="alias-outcome-unknown",
            alias={"name": ALIAS, **observed, "state": "unknown-outcome"},
            recoveryNote=(
                "エイリアス更新の結果を確認できない。自動の戻しはしていない。"
                "alias の version / revisionId は読み直しの観測値で、こちらの更新の結果とは限らない"
                f"（取得できなければ null）。直前の正常版は版 {self.prev['version']}、"
                f"発行済みの版は {self.new_version}（どちらも削除しない）。"
                "人がエイリアス demo の指す版を確認し、必要なら infra/cd/bootstrap.md の手順で戻す。"
            ),
        )
        raise DeployError("エイリアス更新の結果が不明です。観測した状態を記録しました。人が確認してください")

    def rollback(self) -> None:
        assert self.prev is not None
        prev = self.prev
        self.result.update(stage="rollback", rollback="in-progress")
        if self.alias_rev is None:
            self.result.update(rollback="failed")
            return
        try:
            restored = self._lambda(
                "update-alias",
                "--function-name",
                self.function,
                "--name",
                ALIAS,
                "--function-version",
                prev["version"],
                "--revision-id",
                self.alias_rev,
                "--routing-config",
                '{"AdditionalVersionWeights":{}}',
            )
        except AwsError as error:
            self.result.update(
                rollback="failed",
                recoveryNote=(
                    "戻し処理に失敗"
                    + ("（エイリアスが外部で変更された）" if error.conflict else "")
                    + f"。手動復旧が必要。直前の正常版は版 {prev['version']}、"
                    f"今回の版は {self.new_version}。どちらの版も削除しない。"
                ),
            )
            return
        revision = restored.get("RevisionId")
        if (
            restored.get("FunctionVersion") != prev["version"]
            or (restored.get("RoutingConfig") or {}).get("AdditionalVersionWeights")
            or not isinstance(revision, str)
            or not revision
        ):
            self.alias_rev = None
            self.result.update(
                rollback="failed",
                alias={"name": ALIAS, "version": None, "revisionId": None, "state": "unknown-outcome"},
                recoveryNote=(
                    "戻しの応答が期待と一致しない。エイリアスの状態は不明で、スモーク確認はしていない。"
                    f"手動復旧が必要。直前の正常版は版 {prev['version']}、今回の版は {self.new_version}。"
                    "どちらの版も削除しない。"
                ),
            )
            return
        self.alias_rev = revision
        self.result.update(
            alias={
                "name": ALIAS,
                "version": prev["version"],
                "revisionId": self.alias_rev,
                "state": "rolled-back",
            }
        )
        ok = self.smoke(self.base_url, prev["sourceSHA"], self._report_id("rollback"))
        self.result.update(
            rollback="succeeded" if ok else "alias-restored-smoke-failed",
            lastGood=(
                {
                    "version": prev["version"],
                    "sourceSHA": prev["sourceSHA"],
                    "zipSha256": prev["zipSha256"],
                    "smoke": "passed",
                }
                if ok
                else None
            ),
        )

    def run(self) -> int:
        failure: str | None = None
        try:
            self.validate()
            self.check_previous()
            if not self.already_current_check():
                self.mutate()
        except Interrupted:
            failure = "処理が中断されました"
        except DeployError as error:
            failure = str(error)
        except Exception as error:  # 想定外の例外も秘密を出さずに記録する
            failure = f"想定外のエラー: {type(error).__name__}"
        try:
            if failure is not None:
                if self.switched:
                    self.rollback()
                if self.result.data["validation"] != "passed":
                    self.result.data["validation"] = "failed"
                if self.result.data["deployment"] == "in-progress":
                    self.result.data["deployment"] = "failed"
            data = self.result.data
            fresh = (
                failure is None and data["deployment"] == "completed" and data["smoke"] == "passed"
            )
            unchanged = failure is None and self.already_current
            ok = fresh or unchanged
            # 変更なしの成功は、新しい配布の成功と区別する（overall を別の値にする）
            overall = "success" if fresh else "no-change-success" if unchanged else "failed"
            self.result.update(
                overall=overall,
                stage="done" if ok else data["stage"],
                error=failure,
            )
        except Exception:  # 結果の書き込みに失敗しても、終了コードは失敗にする
            return 1
        return 0 if ok else 1


def run_aws(args: list[str]) -> dict:
    command = ["aws", *args, "--region", REGION, "--output", "json", "--no-cli-pager"]
    operation = " ".join(args[:2])
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=300, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise AwsError(operation, -1) from None
    if completed.returncode != 0:
        conflict = "PreconditionFailed" in completed.stderr
        raise AwsError(operation, completed.returncode, conflict)
    if not completed.stdout.strip():
        return {}
    try:
        data = json.loads(completed.stdout)
    except ValueError:
        raise AwsError(operation, 0) from None
    if not isinstance(data, dict):
        raise AwsError(operation, 0)
    return data


def run_smoke(base_url: str, expected_sha: str, report_id: str) -> bool:
    root = Path(__file__).resolve().parent.parent
    # 資格情報を渡さない最小の環境。CD_ALLOW_LOCAL は常に false。
    keep = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "XDG_RUNTIME_DIR")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env.update(
        CD_BASE_URL=base_url,
        CD_EXPECTED_SHA=expected_sha,
        CD_ALLOW_LOCAL="false",
        CD_REPORT_ID=report_id,
    )
    try:
        completed = subprocess.run(
            ["bash", "scripts/cd-smoke.sh"], cwd=root, env=env, timeout=600, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--zip-sha256", required=True, help="ビルドジョブの出力したZIPのSHA-256(16進)")
    parser.add_argument(
        "--verify-artifact-only",
        action="store_true",
        help="成果物（manifestとZIP）だけを検証して終える。AWS CLI・資格情報・通信は使わない",
    )
    # 以下は配布には必須（--verify-artifact-only のときは不要）。不足は validate_inputs で拒否する。
    parser.add_argument("--ci-run-url")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    parser.add_argument("--function-name")
    parser.add_argument("--base-url")
    parser.add_argument("--layer-code-sha256")
    parser.add_argument("--result-file", type=Path)
    return parser.parse_args(argv)


DEPLOY_ONLY_ARGS = (
    "ci_run_url",
    "run_id",
    "run_attempt",
    "function_name",
    "base_url",
    "layer_code_sha256",
    "result_file",
)


def validate_inputs(args: argparse.Namespace) -> None:
    rules = [
        (SHA_RE.fullmatch(args.source_sha), "source-sha"),
        (HEX64_RE.fullmatch(args.zip_sha256), "zip-sha256"),
    ]
    if getattr(args, "verify_artifact_only", False) is not True:
        missing = [name for name in DEPLOY_ONLY_ARGS if getattr(args, name, None) is None]
        if missing:
            raise SystemExit("引数が不足しています: " + ",".join(missing))
        rules += [
            (RUN_RE.fullmatch(args.run_id), "run-id"),
            (RUN_RE.fullmatch(args.run_attempt), "run-attempt"),
            (FUNCTION_RE.fullmatch(args.function_name), "function-name"),
            (BASE_URL_RE.fullmatch(args.base_url), "base-url"),
            (CODE_SHA_RE.fullmatch(args.layer_code_sha256), "layer-code-sha256"),
            (args.ci_run_url.startswith("https://github.com/"), "ci-run-url"),
        ]
    bad = [name for ok, name in rules if not ok]
    if bad:
        raise SystemExit("入力が不正です: " + ",".join(bad))
    if os.environ.get("CD_ALLOW_LOCAL", "") not in ("", "false"):
        raise SystemExit("CD_ALLOW_LOCAL は配布では使えません")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    validate_inputs(args)
    if args.verify_artifact_only:
        # AWS・GitHub・通信を使わない。承認後・資格情報の前に、取得した成果物の改ざんを検出する。
        try:
            load_manifest(args.artifact_dir, args.source_sha, args.zip_sha256)
        except DeployError as error:
            print(f"成果物の検証に失敗: {error}", file=sys.stderr)
            return 1
        print("成果物の検証: 成功（manifest・ZIP・ソースSHA・ビルドのハッシュが一致）")
        return 0

    def on_term(signum, frame):  # noqa: ARG001
        raise Interrupted

    signal.signal(signal.SIGTERM, on_term)
    deployer = Deployer(
        artifact_dir=args.artifact_dir,
        source_sha=args.source_sha,
        zip_hex=args.zip_sha256,
        ci_run_url=args.ci_run_url,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        function_name=args.function_name,
        base_url=args.base_url,
        layer_code_sha256=args.layer_code_sha256,
        result_file=args.result_file,
        aws=run_aws,
        smoke=run_smoke,
    )
    return deployer.run()


if __name__ == "__main__":
    sys.exit(main())
