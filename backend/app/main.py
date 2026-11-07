import os  # 検証用: 未使用importでRuffのlintを失敗させる
from fastapi import FastAPI

app = FastAPI(title="不正審査デモ API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
