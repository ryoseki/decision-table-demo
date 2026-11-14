import json
import os
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import (
    cases,
    db_store,
    intake,
    mock_api,
    question_admin,
    question_proposer,
    question_store,
    rules,
)

app = FastAPI(title="不正審査デモ API")
# DB_HOST があればPostgreSQLに保存し、なければ開発用の仮のメモリ保存（受付のみ）にする。
app.state.case_store = (
    db_store.PostgresCaseStore()
    if os.environ.get("DB_HOST")
    else intake.InMemoryCaseStore()
)
# 管理者AIの試作（設問の追加案）。DBは使わない。
# 生成はモック、保存はメモリ上（再起動で初期化）。
# 実装はここで選んで渡す。HTTP層は共通の形（Protocol）だけを呼ぶ。
app.state.question_proposer = question_proposer.MockQuestionProposer()
app.state.question_store = question_store.InMemoryQuestionStore()
app.include_router(question_admin.router)
app.include_router(mock_api.router)
app.include_router(intake.router)
app.include_router(cases.router)
app.include_router(rules.router)

_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _load_release(release_file: Path) -> dict[str, str | bool]:
    """パッケージ済みの release.json を検証し、公開してよい2項目だけ返す。"""
    try:
        data = json.loads(release_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"RELEASE_FILE を読み込めません: {release_file}") from error
    if not isinstance(data, dict):
        raise RuntimeError("release.json がオブジェクトではありません")
    source_sha = data.get("sourceSHA")
    eligible = data.get("releaseEligible")
    if not isinstance(source_sha, str) or not _SHA_PATTERN.fullmatch(source_sha):
        raise RuntimeError("release.json の sourceSHA が不正です")
    if not isinstance(eligible, bool):
        raise RuntimeError("release.json の releaseEligible が真偽値ではありません")
    return {"sourceSHA": source_sha, "releaseEligible": eligible}


def _enable_packaged_ui(
    application: FastAPI, static_dir: Path, release_file: Path
) -> None:
    index = static_dir / "index.html"
    if not index.is_file() or 'id="root"' not in index.read_text(encoding="utf-8"):
        raise RuntimeError(f"STATIC_DIR に有効な index.html がありません: {index}")
    release = _load_release(release_file)

    @application.get("/release")
    def get_release() -> dict[str, str | bool]:
        return release

    # 静的配信は必ず最後に登録する（/health と /release を隠さないため）。
    # html=True は "/" で index.html を返すだけで、存在しないパスは404のまま
    # （SPAフォールバックはしない）。
    application.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


# STATIC_DIR が未設定なら静的配信も /release も有効にしない（開発時の "/" は404）。
# 設定した場合は RELEASE_FILE も必須で、不備があれば起動時に失敗させる。
_static_dir = os.environ.get("STATIC_DIR")
if _static_dir:
    _release_file = os.environ.get("RELEASE_FILE")
    if not _release_file:
        raise RuntimeError("STATIC_DIR を設定する場合は RELEASE_FILE も必要です")
    _enable_packaged_ui(app, Path(_static_dir), Path(_release_file))
