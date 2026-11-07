# python:3.14.10-slim-trixie（Docker Hub公式イメージ）のマルチアーキテクチャdigestで固定
FROM python:3.14.10-slim-trixie@sha256:af8a5aa14780025dbfbcfbc81bd8a91ebd87711dd5b0f48ed71090949dd7cb1d

WORKDIR /app

# uvはロック生成・同期用のツール。公式イメージ（ghcr.io/astral-sh/uv:0.13.7）の
# マルチアーキテクチャdigestで版と実体を固定して導入する
COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /uvx /bin/

# ロック済みの推移依存をコンテナ内のPythonへ直接同期する
COPY requirements.txt requirements-dev.txt ./
RUN uv pip sync --system requirements.txt requirements-dev.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
