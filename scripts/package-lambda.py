#!/usr/bin/env python3
"""Lambda用ZIPとmanifest.jsonを作る（標準ライブラリのみ）。

通常は infra/cd/Dockerfile の中から実行される（直接実行はしない）。
ZIPは名前順・固定タイムスタンプ・固定パーミッションで作る。同じ入力から同じバイト列に
なることを永続的に保証するものではない（zlibの版などに依存し得る）。
"""

import argparse
import base64
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

RUNTIME = "python3.14"
ARCHITECTURE = "x86_64"
# Lambda Web Adapter v1.1.0（固定コミット）のREADMEにある東京リージョンのx86レイヤー。
# レイヤー実体のハッシュとランタイム互換性は未検証。
ADAPTER = {
    "version": "v1.1.0",
    "commit": "596bdbcea78a89084fd0b522807d04cd2f1f3f7e",
    "layerArn": "arn:aws:lambda:ap-northeast-1:753240598075:layer:LambdaAdapterLayerX86:28",
    "execWrapper": "/opt/bootstrap",
    "handler": "run.sh",
    "port": 8000,
    "layerArtifactHashVerified": False,
    "runtimeCompatibilityVerified": False,
}
# エッジ（CloudFront Functions）のソースをZIPに入れる。配布（deploy-lambda.py）は、LIVEのコードを
# このZIPの中の写しのSHA-256と照合する（ZIPのハッシュで保護される）。
EDGE_ARCNAME = "edge/viewer-ip.js"
HEADER_DEFINITION = re.compile(r'^VIEWER_IP_HEADER = "([a-z0-9-]+)"$', re.MULTILINE)
TOKEN_DEFINITION = re.compile(r'^_INTERNAL_TOKEN_HEADER = b"([a-z0-9-]+)"$', re.MULTILINE)
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
SHARED_OBJECT = re.compile(r"\.so(\.\d+)*$")


def parse_bool(value: str) -> bool:
    if value not in ("true", "false"):
        raise argparse.ArgumentTypeError("true または false を指定してください")
    return value == "true"


def viewer_ip_contract(app_dir: Path, edge_source: bytes) -> bool:
    """アプリとエッジが、2つのヘッダー名で一致しているか。どちらかが食い違えば宣言しない。

    - 接続元IP（usage_quota.py の VIEWER_IP_HEADER）: エッジが利用者の値を捨て、実IPで書く。
    - 内部のモック用トークン（public_guard.py の _INTERNAL_TOKEN_HEADER）: エッジが利用者の値を捨てる。
    """
    quota = app_dir / "usage_quota.py"
    guard = app_dir / "public_guard.py"
    if not quota.is_file() or not guard.is_file():
        return False
    viewer = HEADER_DEFINITION.search(quota.read_text(encoding="utf-8"))
    token = TOKEN_DEFINITION.search(guard.read_text(encoding="utf-8"))
    if viewer is None or token is None:
        return False
    text = edge_source.decode("utf-8", errors="strict")
    return (
        f"headers['{viewer.group(1)}'] = " in text
        and f"lowered === '{viewer.group(1)}'" in text
        and f"lowered === '{token.group(1)}'" in text
        and "function handler(event)" in text
    )


def collect(root: Path, prefix: str, skip_top: frozenset[str] = frozenset()):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[0] in skip_top:
            continue
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise SystemExit(f"シンボリックリンクは含められません: {path}")
        if path.is_file():
            yield f"{prefix}{relative.as_posix()}", path


def mode_for(arcname: str) -> int:
    # 実行権限は run.sh と共有ライブラリだけに付ける（入力側のモードに依存しない）。
    if arcname == "run.sh" or SHARED_OBJECT.search(Path(arcname).name):
        return 0o755
    return 0o644


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deps", type=Path, required=True)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--static", type=Path, required=True)
    parser.add_argument("--run-sh", type=Path, required=True)
    parser.add_argument("--edge-function", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--release-eligible", type=parse_bool, required=True)
    parser.add_argument("--dirty", type=parse_bool, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not SHA_PATTERN.fullmatch(args.source_sha):
        raise SystemExit("--source-sha は40桁の小文字16進数で指定してください")
    if args.release_eligible and args.dirty:
        raise SystemExit("dirty なソースは releaseEligible にできません")
    if not (args.static / "index.html").is_file():
        raise SystemExit("--static に index.html がありません")
    if not (args.app / "main.py").is_file():
        raise SystemExit("--app に main.py がありません")
    if not args.edge_function.is_file() or args.edge_function.is_symlink():
        raise SystemExit("--edge-function が通常のファイルではありません")
    edge_source = args.edge_function.read_bytes()

    release = {
        "sourceSHA": args.source_sha,
        "releaseEligible": args.release_eligible,
        "dirty": args.dirty,
        "runtime": RUNTIME,
        "architecture": ARCHITECTURE,
    }
    release_bytes = (json.dumps(release, indent=2, sort_keys=True) + "\n").encode()

    entries: dict[str, bytes | Path] = {}

    def add(arcname: str, source: bytes | Path) -> None:
        if arcname in entries:
            raise SystemExit(f"ZIP内で名前が重複しています: {arcname}")
        entries[arcname] = source

    # 依存はZIPのルートに置く（Lambda公式のPython ZIPの形）。bin/ のスクリプトは含めない。
    for arcname, path in collect(args.deps, "", frozenset({"bin"})):
        add(arcname, path)
    for arcname, path in collect(args.app, "app/"):
        add(arcname, path)
    for arcname, path in collect(args.static, "static/"):
        add(arcname, path)
    add("run.sh", args.run_sh)
    add(EDGE_ARCNAME, edge_source)
    add("release.json", release_bytes)

    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit("--output は存在しないか空のディレクトリにしてください")
    args.output.mkdir(parents=True, exist_ok=True)
    zip_path = args.output / f"lambda-{args.source_sha}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for arcname in sorted(entries):
            source = entries[arcname]
            data = source if isinstance(source, bytes) else source.read_bytes()
            info = zipfile.ZipInfo(arcname, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100000 | mode_for(arcname)) << 16
            archive.writestr(info, data, compresslevel=9)

    digest = hashlib.sha256(zip_path.read_bytes()).digest()
    # ZIPの実際の内容から導く。CDは、書き込みを許可した公開経路にこの印のない版を出さない
    guard_files = {"app/public_guard.py", "app/usage_quota.py", "app/quota_store.py"}
    has_guard = guard_files <= set(entries)
    manifest = {
        "capabilities": {
            "publicWriteGuard": 1 if has_guard else 0,
            # 専用ヘッダー（x-demo-viewer-ip）の契約。アプリとエッジのヘッダー名が一致するときだけ宣言する
            "viewerIpContract": 1 if has_guard and viewer_ip_contract(args.app, edge_source) else 0,
        },
        "edge": {"file": EDGE_ARCNAME, "sha256Hex": hashlib.sha256(edge_source).hexdigest()},
        "schema": 1,
        "sourceSHA": args.source_sha,
        "dirty": args.dirty,
        "releaseEligible": args.release_eligible,
        "runtime": RUNTIME,
        "architecture": ARCHITECTURE,
        "adapter": ADAPTER,
        "zip": {
            "file": zip_path.name,
            "bytes": zip_path.stat().st_size,
            "entries": len(entries),
            "sha256Hex": digest.hex(),
            # AWS Lambdaの CodeSha256（ZIPのSHA-256のbase64）
            "codeSha256": base64.b64encode(digest).decode("ascii"),
        },
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"ZIP: {zip_path.name} sha256={digest.hex()}")
    print(f"releaseEligible={args.release_eligible}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
