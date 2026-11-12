# python:3.14.10-slim-trixie と uv 0.13.7 は backend/Dockerfile と同じdigestで固定する
FROM python:3.14.10-slim-trixie@sha256:af8a5aa14780025dbfbcfbc81bd8a91ebd87711dd5b0f48ed71090949dd7cb1d

WORKDIR /db

COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /uvx /bin/

COPY requirements.txt ./
RUN uv pip sync --system requirements.txt

COPY verify_schema.py init_dev.py ./

CMD ["python", "verify_schema.py"]
