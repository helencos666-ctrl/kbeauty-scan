"""公开页隐藏医美分类。默认开启，设置 HIDE_MEDICAL_AESTHETIC=false 可恢复。不删任何数据。"""
from __future__ import annotations

import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("COOKIE_INSECURE", "1")
os.environ.pop("DATABASE_URL", None)
os.environ.pop("HIDE_MEDICAL_AESTHETIC", None)

from fastapi.testclient import TestClient
from app import app

ok = fail = 0

def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, str(info)[:400])


def main():
    c = TestClient(app)
    r = c.get("/api/site-flags")
    check("flags 默认隐藏医美", r.status_code == 200 and r.json().get("hide_medical_aesthetic") is True, r.text[:200])

    r = c.get("/api/providers/meta")
    keys = [x["key"] for x in r.json().get("cats") or []]
    check("入驻 meta 不含 clinic 且含其他分类", "clinic" not in keys and "guide" in keys and "food" in keys and "hotel" in keys and len(keys) == 11, keys)

    r = c.get("/api/providers?cat=clinic")
    check("providers?cat=clinic 暂未开放", r.status_code == 200 and r.json().get("unavailable") is True and "暂未开放" in r.json().get("message", ""), r.text[:200])

    r = c.get("/api/shop-examples/clinic")
    check("shop-examples/clinic 暂未开放", r.status_code == 200 and r.json().get("unavailable") is True, r.text[:200])

    r = c.get("/api/shop-examples/guide")
    check("shop-examples/guide 仍可用", r.status_code == 200 and (r.json().get("item") or {}).get("category") == "guide", r.text[:200])

    r = c.get("/api/community/merchant-feed?cat=clinic")
    check("商家信息流 clinic 暂未开放", r.status_code == 200 and r.json().get("unavailable") is True, r.text[:200])
    cats = [x["key"] for x in r.json().get("cats") or []]
    check("商家信息流分类不含医美", "clinic" not in cats and "guide" in cats, cats)

    r = c.get("/api/community/boards")
    boards = [b["key"] for cat in r.json().get("categories") or [] for b in cat.get("boards") or []]
    check("频道列表不含 clinic", "clinic" not in boards and "hotel" in boards, boards)

    r = c.get("/api/community/posts?board=clinic")
    check("clinic 频道帖列表暂未开放", r.status_code == 200 and r.json().get("unavailable") is True, r.text[:200])

    r = c.get("/shops")
    body = r.text
    check("商家页 HTML 不含医美磁贴", 'data-cat="clinic"' not in body and ">医美<" not in body, body[body.find("catChips"):body.find("catChips")+400] if "catChips" in body else "no catChips")
    check("商家页仍有地陪向导等分类", 'data-cat="guide"' in body and 'data-cat="food"' in body and "地陪向导" in body)
    check("商家页轮播不再指向医美", 'href="/shops?cat=clinic"' not in body)
    check("商家页写入 LH_FLAGS", "hide_medical_aesthetic" in body and "window.LH_FLAGS" in body)
    check("商家页无奋韩/站外联系方式", "奋韩" not in body)

    r = c.get("/shops?cat=clinic")
    check("/shops?cat=clinic 页面可打开", r.status_code == 200)
    r = c.get("/shops/examples/clinic")
    check("/shops/examples/clinic 显示暂未开放", r.status_code == 200 and "暂未开放" in r.text)

    r = c.get("/community")
    check("韩友圈首页无医美频道入口", 'board=clinic' not in r.text)

    r = c.get("/partner")
    check("入驻页分类说明不含医美分类名", "医美分类仅限合法医疗机构" not in r.text)

    # 恢复开关后分类重新出现（不改代码、不删数据）
    os.environ["HIDE_MEDICAL_AESTHETIC"] = "false"
    try:
        r = c.get("/api/providers/meta")
        keys = [x["key"] for x in r.json().get("cats") or []]
        check("开关关闭后 clinic 重新出现", "clinic" in keys and len(keys) == 12, keys)
        r = c.get("/shops")
        check("开关关闭后商家页恢复医美入口", 'data-cat="clinic"' in r.text and "医美" in r.text)
        r = c.get("/api/site-flags")
        check("开关关闭后 flags 为 false", r.json().get("hide_medical_aesthetic") is False, r.text[:200])
    finally:
        os.environ.pop("HIDE_MEDICAL_AESTHETIC", None)

    print("%s passed, %s failed" % (ok, fail))
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
