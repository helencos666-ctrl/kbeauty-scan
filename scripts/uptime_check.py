#!/usr/bin/env python3
"""旅韩助手线上巡检（可做成定时任务）：可用性、错误、慢接口、冷启动、数据库、内容是否每天更新。

用法：
    python3 scripts/uptime_check.py                 # 打印完整中文报告
    python3 scripts/uptime_check.py --quiet         # 全部正常时什么都不输出（适合定时任务“没变化就不吭声”）
    python3 scripts/uptime_check.py --json          # 机器可读
    LH_BASE=https://kbeauty-scan.onrender.com python3 scripts/uptime_check.py

退出码：0 = 正常或只有提醒；1 = 有故障（页面/接口打不开、数据库连不上、今日内容没更新等）。
只读：只发 GET 请求，不登录、不写数据库、不读取任何密钥。只依赖 Python 标准库。
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("LH_BASE", "https://lvhanzhushou.com").rstrip("/")
KST = datetime.timezone(datetime.timedelta(hours=9))
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile lvhan-uptime/1"
SLOW, VERY_SLOW, COLD = 3.0, 8.0, 15.0

PAGES = [  # path, 必须出现的文字
    ("/", "旅韩助手"), ("/food", "美食"), ("/attractions", "景点"), ("/shopping", "购物"),
    ("/tips", "攻略"), ("/visa", "签证"), ("/tax-refund", "退税"), ("/faq", "常见问题"),
    ("/community", "韩友圈"), ("/community/rate", "汇率"), ("/shops", "商"), ("/login", "登录"),
    ("/me", "我的"), ("/dazi", "搭子"), ("/festival", "购物节"), ("/trips", "行程"),
]

results = []


def get(path, timeout=40):
    url = BASE + path
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, body, time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"", time.time() - t0
    except Exception as e:  # noqa: BLE001
        return None, str(e).encode(), time.time() - t0


def add(level, name, msg, sec=None):
    results.append({"level": level, "name": name, "msg": msg, "sec": round(sec, 2) if sec is not None else None})


def timing(name, sec):
    if sec >= COLD:
        add("WARN", name, "响应 %.1f 秒，疑似冷启动（服务休眠后被唤醒）" % sec, sec)
    elif sec >= VERY_SLOW:
        add("WARN", name, "响应很慢 %.1f 秒" % sec, sec)
    elif sec >= SLOW:
        add("WARN", name, "响应偏慢 %.1f 秒" % sec, sec)


def jget(path):
    st, body, sec = get(path)
    try:
        return st, json.loads(body.decode("utf-8")), sec
    except Exception:  # noqa: BLE001
        return st, None, sec


def main():
    quiet, as_json = "--quiet" in sys.argv, "--json" in sys.argv
    today = datetime.datetime.now(KST).date()

    # 1) 存活 + 冷启动
    st, body, sec = get("/healthz", timeout=90)
    if st != 200:
        add("FAIL", "/healthz", "健康检查失败 status=%s" % st, sec)
    else:
        add("OK", "/healthz", "在线", sec)
        timing("/healthz", sec)

    # 2) 深度健康：数据库、内容文件、保活
    st, j, sec = jget("/healthz/deep")
    if st == 404:
        add("WARN", "/healthz/deep", "线上还没有深度健康检查（新版本未部署？）", sec)
    elif not j:
        add("FAIL", "/healthz/deep", "深度健康检查无响应 status=%s" % st, sec)
    else:
        db = (j.get("checks") or {}).get("db") or {}
        if not db.get("ok"):
            add("FAIL", "数据库", "数据库连接失败：%s" % db.get("error"), sec)
        else:
            add("OK", "数据库", "连接正常 %sms" % db.get("ms"), sec)
            if (db.get("ms") or 0) > 1500:
                add("WARN", "数据库", "数据库响应慢 %sms" % db.get("ms"))
        bad = [k for k, v in (j.get("checks") or {}).items() if k != "db" and not v.get("ok")]
        if bad:
            add("FAIL", "内容文件", "读取失败：" + "、".join(bad))
        ka = j.get("keepalive") or {}
        if not ka.get("on"):
            add("WARN", "保活", "后台保活未开启，免费实例空闲 15 分钟会休眠")
        elif ka.get("last_ok") is False:
            add("WARN", "保活", "最近一次自检请求失败（累计失败 %s 次）" % ka.get("fail"))
        up = j.get("uptime_s")
        if isinstance(up, int) and up < 600:
            add("WARN", "运行时长", "服务 %d 秒前刚重启（部署或休眠唤醒）" % up)

    # 3) 页面
    for path, kw in PAGES:
        st, body, sec = get(path)
        text = body.decode("utf-8", "replace")
        if st != 200:
            add("FAIL", path, "页面打不开 status=%s" % st, sec)
        elif kw not in text:
            add("FAIL", path, "页面内容异常（缺少「%s」）" % kw, sec)
        else:
            add("OK", path, "200", sec)
            timing(path, sec)

    # 4) 接口
    checks = [
        ("/api/community/feed?tab=latest", lambda j: isinstance(j.get("posts"), list) and len(j["posts"]) > 0, "社区帖子列表为空"),
        ("/api/community/boards", lambda j: bool(j), "频道列表为空"),
        ("/api/providers/meta", lambda j: len(j.get("cats") or []) > 0, "商家分类为空"),
        ("/api/place/search?q=" + urllib.parse.quote("明洞"), lambda j: len(j.get("places") or []) > 0, "地点搜索无结果（地图服务异常？）"),
        ("/static/subway.json", lambda j: bool(j), "地铁数据为空"),
        ("/static/food.json", lambda j: len(j.get("items") or []) > 0, "美食数据为空"),
        ("/static/attractions.json", lambda j: len(j.get("items") or []) > 0, "景点数据为空"),
    ]
    for path, ok_fn, err in checks:
        st, j, sec = jget(path)
        if st != 200 or j is None:
            add("FAIL", path.split("?")[0], "接口失败 status=%s" % st, sec)
        elif not ok_fn(j):
            add("FAIL", path.split("?")[0], err, sec)
        else:
            add("OK", path.split("?")[0], "200", sec)
            timing(path.split("?")[0], sec)

    # 5) 每日内容
    st, j, sec = jget("/api/daily-brief")
    if st == 404:
        add("WARN", "今日速报", "线上还没有今日速报接口（新版本未部署？）", sec)
    elif st != 200 or not j:
        add("FAIL", "今日速报", "接口失败 status=%s" % st, sec)
    else:
        if j.get("date") != today.isoformat():
            add("FAIL", "今日速报", "日期不是今天：%s" % j.get("date"))
        if not j.get("weather"):
            add("WARN", "今日速报", "今天的天气还没取到")
        if not j.get("fx"):
            add("WARN", "今日速报", "今天的参考汇率还没取到")
        else:
            fd = j["fx"].get("date") or ""
            try:
                if (today - datetime.date.fromisoformat(fd)).days > 4:
                    add("WARN", "今日速报", "参考汇率日期较旧：%s" % fd)
            except ValueError:
                pass
        if not j.get("events"):
            add("WARN", "今日速报", "未来 3 周没有收录活动，请补充 static/events.json")
        off = j.get("official") or []
        if not off:
            add("WARN", "小编帖", "没有取到小编帖子")
        else:
            try:
                age = (today - datetime.date.fromisoformat(off[0]["date"])).days
                if age >= 2:
                    add("WARN", "小编帖", "小编已经 %d 天没有发新帖（最近：%s）" % (age, off[0]["title"]))
                else:
                    add("OK", "小编帖", "最近一帖 %s：%s" % (off[0]["date"], off[0]["title"]))
            except Exception:  # noqa: BLE001
                pass
        add("OK", "今日速报", "日期 %s" % j.get("date"), sec)

    for name in ("food.json", "attractions.json"):
        st, j, sec = jget("/static/" + name)
        try:
            age = (today - datetime.date.fromisoformat(j.get("updated"))).days
            if age >= 3:
                add("WARN", name, "已经 %d 天没有新增内容（updated=%s）" % (age, j.get("updated")))
        except Exception:  # noqa: BLE001
            pass

    fails = [r for r in results if r["level"] == "FAIL"]
    warns = [r for r in results if r["level"] == "WARN"]
    status = "异常" if fails else ("有提醒" if warns else "正常")
    if as_json:
        print(json.dumps({"base": BASE, "time": datetime.datetime.now(KST).isoformat(timespec="seconds"),
                          "status": status, "fail": fails, "warn": warns, "all": results}, ensure_ascii=False, indent=1))
    elif not (quiet and not fails and not warns):
        print("旅韩助手巡检 %s（首尔时间）  %s  状态：%s" % (datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M"), BASE, status))
        for r in fails + warns:
            print("  [%s] %s：%s" % ("故障" if r["level"] == "FAIL" else "提醒", r["name"], r["msg"]))
        oks = [r for r in results if r["level"] == "OK"]
        slowest = max((r for r in results if r["sec"] is not None), key=lambda r: r["sec"], default=None)
        print("  通过 %d 项；最慢：%s %.1f 秒" % (len(oks), slowest["name"], slowest["sec"]) if slowest else "  通过 %d 项" % len(oks))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
