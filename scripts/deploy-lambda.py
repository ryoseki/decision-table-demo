#!/usr/bin/env python3
"""承認済みのLambda ZIPを、既存のエイリアス demo へ配布する（標準ライブラリのみ）。

AWS CLI（`aws lambda ...`）をsubprocessで呼ぶ。初回構築・IAM・関数URL・同時実行数・CloudFront・WAFの
変更はしない（infra/cd/bootstrap.md と infra/cd/cloudfront-plan.md の人の操作）。CloudFront・WAF・
予約済み同時実行数（ちょうど3）は読み取り（get-*）だけで、期待と違えばAWSの変更の前に止まる（fail closed）。
公開経路の読み取り専用の検証は、同じディレクトリの `cd_public_path.py` に分けている。

流れ: 検証（成果物・公開経路・関数設定・直前の版）→ 現行版のスモーク確認 → コード更新（検証時の
RevisionIdが条件）→ 完了待ち → 設定とコードのハッシュの確認 → 版の発行（完了後のRevisionIdとコードの
ハッシュが条件）→ 発行した版の確認 → エイリアスの切り替え（検証時のRevisionIdが条件・加重なし）→
スモーク確認。新しい版のスモーク確認が失敗したら、こちらの切り替えが返したRevisionIdを条件に、直前の
正常版へ戻して、戻した版のスモーク確認を行う（戻しが成功しても全体は失敗）。

エイリアスの更新結果が確認できない場合（エラー・想定外の応答・外部の変更）と、強制終了・タイムアウトは、
自動では戻さず、失敗として終え、結果JSONの recoveryNote に人の復旧手順を残す（新しい版を指していても
上書きしない）。エイリアスが既に同じソースSHA・ZIPの版を指し、スモーク確認が合格した場合は、変更しない
成功（overall=no-change-success）で、新しい配布の成功とは扱わない。結果は常に --result-file のJSONへ
段階ごとに書き、Lambdaの設定全体や資格情報は出力・保存しない。
`--verify-artifact-only` は成果物の検証だけを行い、AWS CLI・資格情報・通信を使わない。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

# `python3 -B -I` では、同じ scripts/ のモジュールを自動では読めない。公開経路の検証（読み取り専用）は、
# このファイルと同じディレクトリの cd_public_path.py だけを、場所を固定して読む。
_PUBLIC_PATH_FILE = Path(__file__).resolve().with_name("cd_public_path.py")
_loaded = sys.modules.get("cd_public_path")
if _loaded is None or Path(getattr(_loaded, "__file__", None) or "").resolve() != _PUBLIC_PATH_FILE:
    _spec = importlib.util.spec_from_file_location("cd_public_path", _PUBLIC_PATH_FILE)
    _loaded = importlib.util.module_from_spec(_spec)
    sys.modules["cd_public_path"] = _loaded  # dataclass が参照するため、実行前に登録する
    try:
        _spec.loader.exec_module(_loaded)
    except FileNotFoundError:
        sys.modules.pop("cd_public_path", None)
        raise SystemExit("同じディレクトリの cd_public_path.py がありません（AWSは呼んでいません）")
    except BaseException:
        sys.modules.pop("cd_public_path", None)
        raise
from cd_public_path import (  # noqa: E402
    ALIAS,
    BASE_URL_RE,
    CLOUDFRONT_ID_RE,
    EDGE_FUNCTION_ARN_RE,
    REGION,
    WEB_ACL_ARN_RE,
    AwsRunner,
    DeployError,
    PublicPathCheck,
    check_public_path,
    reserved_concurrency_ok,
)

# CloudFrontとWAF（CLOUDFRONT scope）のAPIはus-east-1。Lambdaは東京。
CF_REGION = "us-east-1"
GLOBAL_SERVICES = ("cloudfront", "wafv2")
RUNTIME = "python3.14"
ARCHITECTURE = "x86_64"
HANDLER = "run.sh"
EXEC_WRAPPER = "/opt/bootstrap"
ADAPTER_PORT = "8000"
# レイヤーはこの固定ARNだけを許す（マニフェストが改ざんされても別のレイヤーにしない）。
PINNED_LAYER_ARN = "arn:aws:lambda:ap-northeast-1:753240598075:layer:LambdaAdapterLayerX86:28"

# エッジ（CloudFront Functions）のソースをZIPに入れる場所。LIVEのコードとの照合は cd_public_path.py。
EDGE_ARTIFACT_PATH = "edge/viewer-ip.js"

SHA_RE = re.compile(r"[0-9a-f]{40}")
HEX64_RE = re.compile(r"[0-9a-f]{64}")
CODE_SHA_RE = re.compile(r"[A-Za-z0-9+/]{43}=")
VERSION_RE = re.compile(r"[1-9][0-9]{0,17}")
RUN_RE = re.compile(r"[0-9]{1,20}")
FUNCTION_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
DESCRIPTION_RE = re.compile(
    r"source=(?P<source>[0-9a-f]{40});zip=(?P<zip>[0-9a-f]{64});run=(?P<run>[0-9]{1,20})"
    r"(?P<guard>;guard=1)?(?P<viewerip>;viewerip=1)?"
)

SmokeRunner = Callable[[str, str, str], bool]


class AwsError(DeployError):
    def __init__(self, operation: str, returncode: int, conflict: bool = False):
        super().__init__(f"AWS操作に失敗: {operation} (終了コード {returncode})")
        self.operation = operation
        self.conflict = conflict


def make_description(
    source_sha: str, zip_hex: str, run_id: str, guard: bool = False, viewer_ip: bool = False
) -> str:
    """版のdescription。

    guard=True: 公開書き込みのガード・利用枠を含むZIPを、この配布が検証して発行した印。
    viewer_ip=True: 接続元IPの契約（専用ヘッダー x-demo-viewer-ip・ポートなしのIP1つ。
    エッジのソースがZIPに入り、manifestとZIPが一致する）を満たすZIPを、この配布が検証して発行した印。
    """
    text = f"source={source_sha};zip={zip_hex};run={run_id}"
    if guard:
        text += ";guard=1"
    if viewer_ip:
        text += ";viewerip=1"
    return text


def parse_description(text: object) -> dict[str, str] | None:
    if not isinstance(text, str):
        return None
    match = DESCRIPTION_RE.fullmatch(text)
    if not match:
        return None
    return {key: value for key, value in match.groupdict().items() if value is not None}


def config_problems(config: dict, *, require_status: bool = False) -> list[str]:
    """検査対象の設定（関数・版の共通）。環境変数の値は保持せず、問題の名前だけ返す。

    require_status=True なら、State=Active・LastUpdateStatus=Successful の明示的な値を必須にする
    （キーがない・null は問題）。更新の完了後の最終確認で使う。既定は、キーがなければ許す。
    """
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
    allowed_state = ("Active",) if require_status else (None, "Active")
    allowed_update = ("Successful",) if require_status else (None, "Successful")
    if config.get("State") not in allowed_state:
        problems.append("state")
    if config.get("LastUpdateStatus") not in allowed_update:
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


def manifest_has_write_guard(manifest: dict) -> bool:
    """manifest（ZIPのハッシュと照合済み）が、公開書き込みのガードと利用枠を含むと宣言しているか。"""
    capabilities = manifest.get("capabilities")
    return isinstance(capabilities, dict) and capabilities.get("publicWriteGuard") == 1


def manifest_has_viewer_ip_contract(manifest: dict) -> bool:
    """manifest（ZIPのハッシュ・中のエッジのソースと照合済み）が、接続元IPの契約を宣言しているか。"""
    capabilities = manifest.get("capabilities")
    return (
        isinstance(capabilities, dict)
        and capabilities.get("viewerIpContract") == 1
        and capabilities.get("publicWriteGuard") == 1
    )


def zip_edge_source(zip_path: Path) -> bytes:
    """ZIPの中のエッジ関数のソース（レビュー済みのものを、ビルドが入れた写し）。"""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            return archive.read(EDGE_ARTIFACT_PATH)
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError):
        raise DeployError("ZIPにエッジ関数のソースがありません") from None


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
    # 接続元IPの契約を宣言するmanifestは、ZIPの中のエッジのソースのSHA-256をmanifestと照合する。
    # 以後、LIVEのコードと照合するのは、manifestの値ではなく、このZIPの中の写し。
    capabilities = manifest.get("capabilities")
    if isinstance(capabilities, dict) and capabilities.get("viewerIpContract") == 1:
        edge = manifest.get("edge")
        recorded = edge.get("sha256Hex") if isinstance(edge, dict) else None
        embedded = hashlib.sha256(zip_edge_source(zip_path)).hexdigest()
        if (
            not isinstance(edge, dict)
            or edge.get("file") != EDGE_ARTIFACT_PATH
            or recorded != embedded
            or capabilities.get("publicWriteGuard") != 1
        ):
            raise DeployError("manifest検証に失敗: edgeSource")
    return zip_path, manifest


class Result:
    """結果JSON。段階ごとにアトミックに書き直す（強制終了でも直前までの記録が残る）。"""

    def __init__(self, path: Path, data: dict):
        self.path = path
        self.data = data

    def update(self, **fields: object) -> None:
        self.data.update(fields)
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
    """`args` は parse_args の結果（入力は validate_inputs で検証済み）。aws・smoke は差し替え可能。"""

    def __init__(self, args: argparse.Namespace, aws: AwsRunner, smoke: SmokeRunner):
        self.a = args
        self.aws = aws
        self.smoke = smoke
        self.code_sha = code_sha_from_hex(args.zip_sha256)
        self.edge_arn = args.edge_function_arn or None
        self.prev: dict[str, str] = {}  # version / source / zip / aliasRevision（検証で確定）
        self.switched_revision: str | None = None  # こちらの切り替えが返したRevisionId（戻しの根拠）
        self.no_change = False
        self.write_enabled = False  # 公開経路が書き込みメソッドを許可しているか（検証で確定）
        self.guard = self.viewer_ip = False  # 今回のZIPが持つ印（manifestで確認）
        self.edge_source: bytes | None = None  # ZIPの中のレビュー済みエッジのソース（契約があるときだけ）
        self.result = Result(
            args.result_file,
            {
                "schema": 2,
                "sourceSHA": args.source_sha,
                "zipSha256": args.zip_sha256,
                "ciRunUrl": args.ci_run_url,
                "runId": args.run_id,
                "runAttempt": args.run_attempt,
                "overall": "in-progress",
                "stage": "start",
                # AWSの変更操作を始める直前に true にする。成功を意味しない。
                "mutationStarted": False,
                "versions": {"previous": None, "new": None},
                # untouched / unknown（切り替え中・結果不明・外部の変更）/ switched / rolled-back
                "alias": "untouched",
                "smoke": {"previous": "not-run", "new": "not-run", "rollback": "not-run"},
                "rollback": "not-needed",
                "note": None,
                "error": None,
                "recoveryNote": None,
            },
        )

    def lam(self, *args: str) -> dict:
        return self.aws(["lambda", *args])

    def stage(self, name: str) -> None:
        self.result.update(stage=name)

    def run_smoke(self, label: str, expected_sha: str) -> bool:
        self.stage(f"smoke-{label}")
        a = self.a
        ok = self.smoke(a.base_url, expected_sha, f"run{a.run_id}-{a.run_attempt}-{label}")
        self.result.data["smoke"][label] = "passed" if ok else "failed"
        self.result.update()
        return ok

    # --- 検証（変更なし） ---
    def validate(self) -> None:
        a = self.a
        self.stage("validate")
        self.zip_path, manifest = load_manifest(a.artifact_dir, a.source_sha, a.zip_sha256)
        self.guard = manifest_has_write_guard(manifest)
        self.viewer_ip = manifest_has_viewer_ip_contract(manifest)
        # LIVEのコードと照合する「レビュー済みのソース」は、ハッシュ検証済みのZIPの中の写し
        self.edge_source = zip_edge_source(self.zip_path) if self.viewer_ip else None

        url = self.lam("get-function-url-config", "--function-name", a.function_name, "--qualifier", ALIAS)
        self.write_enabled = check_public_path(
            self.aws,
            PublicPathCheck(
                function_name=a.function_name,
                base_url=a.base_url,
                distribution_id=a.distribution_id,
                oac_id=a.oac_id,
                web_acl_arn=a.web_acl_arn,
                function_url=url,
                edge_function_arn=self.edge_arn,
                edge_source=self.edge_source,
            ),
        )

        config = self.lam("get-function-configuration", "--function-name", a.function_name)
        problems = config_problems(config)
        self.function_revision = config.get("RevisionId")
        if not isinstance(self.function_revision, str) or not self.function_revision:
            problems.append("revisionId")
        if problems:
            raise DeployError("関数設定が期待と一致しません: " + ",".join(problems))

        # 予約済み同時実行数は読み取りだけ。自動では設定・修正しない
        if not reserved_concurrency_ok(self.lam("get-function-concurrency", "--function-name", a.function_name)):
            raise DeployError(
                "予約済み同時実行数がちょうど3ではありません（未設定・0を含む）。"
                "配布ヘルパーは同時実行数を変更しません（人が確認してください）"
            )
        layer = self.lam("get-layer-version-by-arn", "--arn", PINNED_LAYER_ARN)
        if (layer.get("Content") or {}).get("CodeSha256") != a.layer_code_sha256:
            raise DeployError("レイヤーのCodeSha256が記録した期待値と一致しません")

        alias = self.lam("get-alias", "--function-name", a.function_name, "--name", ALIAS)
        version, revision = alias.get("FunctionVersion"), alias.get("RevisionId")
        if (alias.get("RoutingConfig") or {}).get("AdditionalVersionWeights"):
            raise DeployError("エイリアスが加重ルーティングです。人が確認してください")
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            raise DeployError("エイリアスの指す版が数値の版ではありません")
        if not isinstance(revision, str) or not revision:
            raise DeployError("エイリアスのRevisionIdを取得できません")
        old = self.lam("get-function-configuration", "--function-name", a.function_name, "--qualifier", version)
        meta = parse_description(old.get("Description"))
        if meta is None:
            raise DeployError("直前の版に利用できる出所情報（description）がありません")
        if old.get("CodeSha256") != code_sha_from_hex(meta["zip"]):
            raise DeployError("直前の版のコードと出所情報のハッシュが一致しません")
        if self.write_enabled:
            # 書き込み可能な公開経路では、今回の版も戻し先の版も、ガード・利用枠（guard=1）と接続元IPの
            # 契約（viewerip=1）の両方の印を持つ版だけを許す。古い版は、読み取り構成（許可メソッド3種）で
            # 配布して印を付けてから書き込みへ切り替える。
            missing = [
                name
                for name, present in (
                    ("candidateWithoutWriteGuard", self.guard),
                    ("candidateWithoutViewerIpContract", self.viewer_ip),
                    ("rollbackTargetWithoutWriteGuard", meta.get("guard")),
                    ("rollbackTargetWithoutViewerIpContract", meta.get("viewerip")),
                )
                if not present
            ]
            if missing:
                raise DeployError(
                    "書き込みを許可した公開経路では、ガード・利用枠と接続元IPの契約を含まない版を"
                    "配布・戻し先にできません: " + ",".join(missing)
                )
        self.prev = {"version": version, "source": meta["source"], "zip": meta["zip"], "aliasRevision": revision}
        self.result.update(versions={"previous": version, "new": None})

    # --- 変更 ---
    def deploy(self) -> None:
        a, prev, r = self.a, self.prev, self.result
        if not self.run_smoke("previous", prev["source"]):
            raise DeployError("現在のエイリアスの版が正常ではないため、変更しませんでした")
        if prev["source"] == a.source_sha and prev["zip"] == a.zip_sha256:
            self.no_change = True
            r.update(
                stage="done",
                note="エイリアスは既に同じソースSHA・ZIPの版を指し、その版のスモーク確認は合格した。"
                "変更なしの成功で、新しい版は発行しておらず、新しい配布の成功ではない。",
            )
            return

        # 検証時に取得したRevisionIdを条件にする。以後に関数が変わっていれば更新は失敗する
        r.update(stage="update-code", mutationStarted=True)
        updated = self.lam(
            "update-function-code", "--function-name", a.function_name,
            "--zip-file", f"fileb://{self.zip_path}", "--revision-id", self.function_revision,
        )  # fmt: skip
        if updated.get("CodeSha256") != self.code_sha:
            raise DeployError("アップロード後のCodeSha256がmanifestと一致しません")
        # この応答のRevisionIdは、完了後と同じとは限らない（実際のCDで不一致を観測）。比較にも発行の条件にも使わない。
        self.stage("wait-update")
        self.lam("wait", "function-updated-v2", "--function-name", a.function_name)
        current = self.lam("get-function-configuration", "--function-name", a.function_name)
        problems = config_problems(current, require_status=True)
        if current.get("CodeSha256") != self.code_sha:
            problems.append("codeSha256")
        completed = current.get("RevisionId")  # 完了後に読み直した値。発行の条件にする
        if not isinstance(completed, str) or not completed:
            problems.append("revisionId")
        if problems:
            raise DeployError("コード更新後の関数の状態が期待と一致しません（発行しません）: " + ",".join(problems))

        self.stage("publish")
        description = make_description(a.source_sha, a.zip_sha256, a.run_id, self.guard, self.viewer_ip)
        published = self.lam(
            "publish-version", "--function-name", a.function_name,
            "--code-sha256", self.code_sha, "--revision-id", completed, "--description", description,
        )  # fmt: skip
        version = published.get("Version")
        if (
            not isinstance(version, str)
            or not VERSION_RE.fullmatch(version)
            or published.get("CodeSha256") != self.code_sha
        ):
            raise DeployError("発行された版の内容が期待と一致しません")
        # 発行直後の版は Pending のことがある。Active になるまで待ってから確認する（待機の失敗はエイリアスを動かさず止まる）
        self.stage("wait-published")
        self.lam("wait", "function-active-v2", "--function-name", a.function_name, "--qualifier", version)
        self._verify_published(version)
        r.update(versions={"previous": prev["version"], "new": version})

        # 切り替え。結果を確認できなければ戻さず、失敗として終える（人が復旧する）
        self.stage("switch-alias")
        self.switched_revision = self._set_alias(version, prev["aliasRevision"])
        r.update(alias="switched")
        if not self.run_smoke("new", a.source_sha):
            raise DeployError("新しい版のスモーク確認に失敗しました")

    def _verify_published(self, version: str) -> None:
        """発行された版が、今回のソース・ZIP・検査済みの設定の版と確認できるか（確認できなければ止まる）。

        AWSの公式資料は、コードも設定も変わらないと新しい版を発行しないと述べるが、既存の版を返すとは
        明記していない（未確認）。返された版は、description（run番号以外）・コードのハッシュ・検査対象の
        設定がすべて今回と一致すると確認できたときだけ使う。エイリアスの現在の版や、確認できない版は、
        止まって人が確認する（設定を変えて版を作らせない）。
        """
        a = self.a
        config = self.lam("get-function-configuration", "--function-name", a.function_name, "--qualifier", version)
        meta = parse_description(config.get("Description")) or {}
        problems = config_problems(config)
        if config.get("CodeSha256") != self.code_sha:
            problems.append("codeSha256")
        expected = (a.source_sha, a.zip_sha256, self.guard, self.viewer_ip)
        if (meta.get("source"), meta.get("zip"), bool(meta.get("guard")), bool(meta.get("viewerip"))) != expected:
            problems.append("description")
        if version == self.prev["version"]:
            problems.append("sameAsCurrentVersion")
        if problems:
            raise DeployError(
                f"発行された版 {version} を今回のソース・設定と証明できません: {','.join(problems)}。"
                "手動で確認してください（infra/cd/bootstrap.md の「発行済みの版が既にある場合」）"
            )

    def _set_alias(self, version: str, expected_revision: str) -> str:
        """エイリアス demo を、RevisionIdを条件に、加重なしで指定の版へ向ける。応答を確認できたときだけ新しいRevisionIdを返す。

        呼ぶ前に alias を unknown にして復旧メモを残す（強制終了・タイムアウトでも、結果不明として残る）。
        失敗・想定外の応答・条件の不一致（外部の変更）はすべて例外で、戻しの根拠にはならない。
        """
        a, prev = self.a, self.prev
        self.result.update(
            alias="unknown",
            recoveryNote=(
                "エイリアス demo の更新結果を確認できていない。自動では戻していない。"
                f"`aws lambda get-alias` で指す版を確認する（直前の正常版は版 {prev['version']}、"
                f"今回の版は {self.result.data['versions']['new']}。どちらも削除しない）。"
                "必要なら infra/cd/bootstrap.md の手順で、現在のRevisionIdを条件に `update-alias` で戻す。"
            ),
        )
        try:
            response = self.lam(
                "update-alias", "--function-name", a.function_name, "--name", ALIAS,
                "--function-version", version, "--revision-id", expected_revision,
                "--routing-config", '{"AdditionalVersionWeights":{}}',
            )  # fmt: skip
        except AwsError as error:
            raise DeployError(
                "エイリアスが外部で変更されたため更新されませんでした（実際の版は不明）"
                if error.conflict
                else "エイリアス更新の結果が不明です。人が確認してください"
            ) from None
        revision = response.get("RevisionId")
        if (
            response.get("FunctionVersion") != version
            or (response.get("RoutingConfig") or {}).get("AdditionalVersionWeights")
            or not isinstance(revision, str)
            or not revision
        ):
            raise DeployError("エイリアス更新の応答が期待と一致せず、結果が不明です。人が確認してください")
        return revision

    def rollback(self) -> None:
        """こちらの切り替えが返したRevisionIdを条件に、直前の正常版へ戻し、戻した版のスモーク確認をする。"""
        prev, r = self.prev, self.result
        r.update(stage="rollback", rollback="in-progress")
        try:
            self._set_alias(prev["version"], self.switched_revision)
        except DeployError:
            r.update(rollback="failed")  # alias=unknown と recoveryNote は _set_alias が残している
            return
        r.update(alias="rolled-back")
        if self.run_smoke("rollback", prev["source"]):
            r.update(rollback="succeeded", recoveryNote=None)
        else:
            r.update(rollback="alias-restored-smoke-failed")

    def run(self) -> int:
        failure: str | None = None
        try:
            self.validate()
            self.deploy()
        except DeployError as error:
            failure = str(error)
        except Exception as error:  # 想定外の例外も秘密を出さずに記録する
            failure = f"想定外のエラー: {type(error).__name__}"
        try:
            if failure is not None and self.switched_revision is not None:
                self.rollback()
            if failure is None:
                self.result.update(overall="no-change-success" if self.no_change else "success", stage="done", recoveryNote=None)
            else:
                self.result.update(overall="failed", error=failure)
        except Exception:  # 結果の書き込みに失敗しても、終了コードは失敗にする
            return 1
        return 0 if failure is None else 1


def run_aws(args: list[str]) -> dict:
    operation = " ".join(args[:2])
    if "--region" in args:  # リージョンはここだけで決める（重複させない）
        raise AwsError(operation, -1)
    region = CF_REGION if args and args[0] in GLOBAL_SERVICES else REGION
    command = ["aws", *args, "--region", region, "--output", "json", "--no-cli-pager"]
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
    parser.add_argument("--base-url", help="公開URL（https://<id>.cloudfront.net のオリジンのみ）")
    parser.add_argument("--distribution-id", help="CloudFrontのディストリビューションID")
    parser.add_argument("--oac-id", help="Origin Access ControlのID")
    parser.add_argument("--web-acl-arn", help="WAF（CLOUDFRONT scope、us-east-1）のWeb ACLのARN")
    parser.add_argument("--layer-code-sha256")
    parser.add_argument(
        "--edge-function-arn",
        help="承認したCloudFront Functions（viewer-request）のARN。書き込み構成（7メソッド）では必須、"
        "読み取り構成では未指定でよい",
    )
    parser.add_argument("--result-file", type=Path)
    return parser.parse_args(argv)


# 入力名 -> 完全一致する形式（--verify-artifact-only では先頭の2つだけ検証する）
INPUT_FORMATS = {
    "source_sha": SHA_RE,
    "zip_sha256": HEX64_RE,
    "ci_run_url": re.compile(r"https://github\.com/.+"),
    "run_id": RUN_RE,
    "run_attempt": RUN_RE,
    "function_name": FUNCTION_RE,
    "base_url": BASE_URL_RE,
    "distribution_id": CLOUDFRONT_ID_RE,
    "oac_id": CLOUDFRONT_ID_RE,
    "web_acl_arn": WEB_ACL_ARN_RE,
    "layer_code_sha256": CODE_SHA_RE,
}


def validate_inputs(args: argparse.Namespace) -> None:
    verify_only = args.verify_artifact_only is True
    bad = [
        name.replace("_", "-")
        for name in list(INPUT_FORMATS)[: 2 if verify_only else None]
        if not isinstance(getattr(args, name, None), str) or not INPUT_FORMATS[name].fullmatch(getattr(args, name))
    ]
    if not verify_only:
        edge = args.edge_function_arn
        if edge not in (None, "") and not (isinstance(edge, str) and EDGE_FUNCTION_ARN_RE.fullmatch(edge)):
            bad.append("edge-function-arn")
        if args.result_file is None:
            bad.append("result-file")
    if bad:
        raise SystemExit("入力が不正または不足しています: " + ",".join(bad))
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
    return Deployer(args, run_aws, run_smoke).run()


if __name__ == "__main__":
    sys.exit(main())
