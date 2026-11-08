# Lambda ZIP（Python 3.14、x86_64）のビルド。ビルドコンテキストはリポジトリルート。
# scripts/build-lambda.sh が使う。成果物は --target artifact --output type=local で取り出す。

# 1. フロントエンド（既存の frontend/Dockerfile と同じNodeのdigest）
FROM node:26.13.0-trixie-slim@sha256:5adad64f9a1fca2d51972caa65983634c8a774eb300d72b20f7f2a0f60368072 AS frontend-build
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN npm run build

# 2. 本番依存。ホストのネイティブwheelではなく、Lambda（Linux x86_64）向けのwheelだけを
#    ハッシュ検証つきで取得する。既存の backend/Dockerfile と同じPythonのdigest。
FROM python:3.14.10-slim-trixie@sha256:af8a5aa14780025dbfbcfbc81bd8a91ebd87711dd5b0f48ed71090949dd7cb1d AS python-deps
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes \
      --platform manylinux2014_x86_64 --implementation cp --python-version 3.14 --abi cp314 \
      --target /deps -r /tmp/requirements.txt

# 3. ZIPとマニフェストの作成（標準ライブラリのみ）
FROM python:3.14.10-slim-trixie@sha256:af8a5aa14780025dbfbcfbc81bd8a91ebd87711dd5b0f48ed71090949dd7cb1d AS package
ARG SOURCE_SHA
ARG RELEASE_ELIGIBLE
ARG DIRTY
COPY scripts/package-lambda.py /tool/package-lambda.py
COPY --from=python-deps /deps /in/deps
COPY backend/app /in/app
COPY --from=frontend-build /src/dist /in/static
COPY infra/cd/run.sh /in/run.sh
RUN python -I /tool/package-lambda.py \
      --deps /in/deps --app /in/app --static /in/static --run-sh /in/run.sh \
      --source-sha "$SOURCE_SHA" --release-eligible "$RELEASE_ELIGIBLE" --dirty "$DIRTY" \
      --output /out

# 4. 取り出し用（ZIPとmanifest.jsonだけ）
FROM scratch AS artifact
COPY --from=package /out/ /
