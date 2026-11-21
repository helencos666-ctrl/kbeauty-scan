"""对外功能开关。

HIDE_MEDICAL_AESTHETIC 默认开启：对外隐藏「医美」分类与相关入口，不删除任何用户或商家数据。
恢复展示：设置环境变量 HIDE_MEDICAL_AESTHETIC=false，或把下面的默认值改为 False。
后台 /api/admin/* 不受此开关影响。
"""
from __future__ import annotations

import os

# 默认隐藏。改 False 即可在不设环境变量的情况下恢复对外展示。
HIDE_MEDICAL_AESTHETIC_DEFAULT = True

MEDICAL_AESTHETIC_CAT = "clinic"
MEDICAL_AESTHETIC_BOARD = "clinic"
UNAVAILABLE_MSG = "该项目暂未开放"
UNAVAILABLE_DETAIL = "医美项目暂时不对外开放，给您带来不便，请见谅。"

_FEATURE_BLOCK = (
    r"<!--\s*feature:medical-aesthetic\s*-->"
    r".*?"
    r"<!--\s*/feature:medical-aesthetic\s*-->"
)


def _env_flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or not str(v).strip():
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def hide_medical_aesthetic() -> bool:
    return _env_flag("HIDE_MEDICAL_AESTHETIC", HIDE_MEDICAL_AESTHETIC_DEFAULT)


def hidden_cats() -> frozenset:
    return frozenset({MEDICAL_AESTHETIC_CAT}) if hide_medical_aesthetic() else frozenset()


def hidden_boards() -> frozenset:
    return frozenset({MEDICAL_AESTHETIC_BOARD}) if hide_medical_aesthetic() else frozenset()


def is_hidden_cat(cat: str) -> bool:
    c = (cat or "").strip()
    if c in ("booking", "transfer", "assist"):
        return False
    return c in hidden_cats()


def is_hidden_board(board: str) -> bool:
    return (board or "").strip() in hidden_boards()


def is_hidden_post(row) -> bool:
    if not row:
        return False
    return is_hidden_board(row.get("board")) or is_hidden_cat(row.get("merchant_cat") or "")


def public_flags() -> dict:
    return {
        "hide_medical_aesthetic": hide_medical_aesthetic(),
        "hidden_cats": sorted(hidden_cats()),
        "hidden_boards": sorted(hidden_boards()),
        "unavailable_message": UNAVAILABLE_MSG,
        "unavailable_detail": UNAVAILABLE_DETAIL,
    }


def apply_public_html_flags(html_text: str, filename: str = "") -> str:
    """去掉公开页里标记为医美入口的 HTML 片段。管理后台页面不处理。"""
    import re
    name = (filename or "").replace("\\", "/").split("/")[-1]
    if name in ("admin.html", "admin-ops.js"):
        return html_text
    if not hide_medical_aesthetic():
        return html_text
    return re.sub(_FEATURE_BLOCK, "", html_text, flags=re.S)


def unavailable_payload(**extra):
    out = {"unavailable": True, "message": UNAVAILABLE_MSG, "detail": UNAVAILABLE_DETAIL}
    out.update(extra)
    return out
