"""py-unsafe-deserialization の検査用の架空コード。実行しない（Semgrepが読むだけ）。"""

import json
import marshal
import pickle

import yaml


def violations(blob, stream):
    # ruleid: py-unsafe-deserialization
    pickle.loads(blob)
    # ruleid: py-unsafe-deserialization
    pickle.load(stream)
    # ruleid: py-unsafe-deserialization
    marshal.loads(blob)
    # ruleid: py-unsafe-deserialization
    marshal.load(stream)
    # ruleid: py-unsafe-deserialization
    yaml.load(stream)
    # ruleid: py-unsafe-deserialization
    yaml.load(stream, Loader=yaml.Loader)


def controls(blob, stream):
    # ok: py-unsafe-deserialization
    json.loads(blob)
    # ok: py-unsafe-deserialization
    yaml.safe_load(stream)
    # ok: py-unsafe-deserialization
    yaml.load(stream, Loader=yaml.SafeLoader)
    # ok: py-unsafe-deserialization
    yaml.load(stream, Loader=yaml.CSafeLoader)
    # ok: py-unsafe-deserialization
    yaml.load(stream, yaml.SafeLoader)
