#!/usr/bin/env python3
"""每日选题助手：从官方公开数据里找出「今天值得写」的话题，输出 Markdown，供每日内容例行任务写官方帖时参考。

数据源（都是公开页面/接口，不登录）：
- 首尔市官方旅游网 VisitSeoul 活动日历（今天起 4 周的庆典、今天在展的展览）
- 本站 static/events.json（人工核对的官方活动）
- 欧洲央行参考汇率（汇率明显变化时提示可以写换钱帖）

用法：
    python3 scripts/daily_topics.py                       # 打印到屏幕
    python3 scripts/daily_topics.py >> /workspace/lvhan-hot-topics.md
只给选题和来源链接，不自动发帖；帖子正文仍需人工/例行任务按来源核实后撰写。
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("DATABASE_URL", None)
import daily_brief as B  # noqa: E402


def main():
    d = B.today()
    lines = ["", "## %s 选题候选（scripts/daily_topics.py 自动生成，仅官方公开数据）" % d.isoformat()]
    try:
        vs = B.fetch_visitseoul()
    except Exception as e:  # noqa: BLE001
        vs = {"events": [], "exhibitions": []}
        lines.append("- VisitSeoul 活动接口今天没取到（%s），只用本站活动表。" % type(e).__name__)
    evs = B.events_for(d, horizon=28, limit=20, extra=vs.get("events"))
    starting = [e for e in evs if e["status"] == "upcoming" and e["days_to_start"] <= 7]
    ending = [e for e in evs if e["status"] == "ongoing" and e["days_left"] <= 5]
    ongoing = [e for e in evs if e["status"] == "ongoing" and e not in ending]
    def fmt(e, extra=""):
        return "- %s（%s～%s，%s）%s 来源：%s %s" % (e["title"], e["start"], e["end"], e.get("place", ""), extra, e.get("source", ""), e.get("source_url", ""))
    if starting:
        lines.append("### 一周内开始（适合写「怎么去 / 半日路线」）")
        lines += [fmt(e, "· %d 天后开始" % e["days_to_start"]) for e in starting]
    if ending:
        lines.append("### 快结束了（适合写「最后几天别错过」类提醒，注意不用绝对化用词）")
        lines += [fmt(e, "· 还剩 %d 天" % (e["days_left"] + 1)) for e in ending]
    if ongoing:
        lines.append("### 正在进行")
        lines += [fmt(e) for e in ongoing[:6]]
    exh = vs.get("exhibitions") or []
    if exh:
        lines.append("### 今天在展的展览（任选 1–2 个写「雨天/室内去处」）")
        lines += [fmt(e) for e in exh[:8]]
    try:
        fx = B.fetch_fx()
        lines.append("### 参考汇率")
        lines.append("- 1 人民币 ≈ %s 韩元（%s，%s）%s" % (fx["cny_krw"], fx["source"], fx["date"], fx["source_url"]))
    except Exception:  # noqa: BLE001
        pass
    print("\n".join(lines))


if __name__ == "__main__":
    main()
