from __future__ import annotations

from typing import Any, Iterable

from fastapi import HTTPException


def pick(payload: dict, allowed: Iterable[str]) -> dict[str, Any]:
    out = {}
    for k in allowed:
        if k in payload:
            v = payload[k]
            if isinstance(v, str):
                v = v.strip()
            out[k] = v
    return out


def num_or_none(v):
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"数字格式不正确：{v}") from e


def must(row, what: str = "记录"):
    if not row:
        raise HTTPException(404, f"{what}不存在")
    return row
