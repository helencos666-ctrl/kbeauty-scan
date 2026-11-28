"""第14届首尔国际电影大奖：python test/test_seoul_movie_awards.py"""
import datetime, json, os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
ok = fail = 0

def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, str(info)[:400])

def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()

ev = json.loads(read("static", "events.json"))
item = next(i for i in ev["items"] if i["id"] == "seoul-global-movie-awards-2026")
posts = json.loads(read("official_daily.json"))
post = next(p for p in posts if p["image"] == "2026-11-28-seoul-movie-awards.jpg")
img = os.path.join(ROOT, "static", "official-daily", "2026-11-28-seoul-movie-awards.jpg")
blob = json.dumps(item, ensure_ascii=False) + json.dumps(post, ensure_ascii=False)

check("标题为第14届首尔国际电影大奖", item["title"] == "2026 第14届首尔国际电影大奖")
check("标题不含电影节", "电影节" not in item["title"] and "电影节" not in post["title"])
check("日期为 2026-12-17 单日", item["start"] == "2026-12-17" and item["end"] == "2026-12-17")
check("时间写明下午5点/17:00", "17:00" in item["hours"])
check("地址含华克山庄路177号和 Vista大厅", "华克山庄路177号" in item["place"] and "Vista大厅" in item["place"])
check("交通写在提示里", "广纳鲁" in item["tip"] and "江边站" in item["tip"] and "免费班车" in item["tip"])
check("主办为韩国电影演员协会", "韩国电影演员协会" in item["tip"])
check("参加方式：邀请5位好友注册领名额", "邀请5位好友" in item["fee"] and "邀请5位好友" in item["tip"] and "最终以主办方确认为准" in item["tip"])
check("不再写未对公众售票", "未见对公众公开售票" not in blob and "没有查到面向一般游客的公开售票" not in blob)
check("事件页入口写在提示里", "/event/seoul-movie-awards" in item["tip"])
check("来源为可访问的 https 链接", item["source_url"].startswith("https://") and "yna.co.kr" in item["source_url"])
check("不含往届参与明星或智秀海报用语", "往届参与明星" not in blob and "智秀" not in blob and "BLACKPINK" not in blob)
check("未引用活动海报图", "poster" not in blob.lower() and "poster.png" not in read("official_daily.json"))
check("官方帖标题一致且不超过40字", post["title"] == item["title"] and 1 <= len(post["title"]) <= 40)
check("官方帖在出行提醒频道", post["board"] == "news" and post["date"] == "2026-11-28")
check("官方帖含投票链接和参加方式", "https://sgmafans.com/" in post["body"] and "邀请5位好友" in post["body"] and "最终以主办方确认为准" in post["body"] and "https://lvhanzhushou.com/event/seoul-movie-awards" in post["body"])
check("官方帖标记 sync 以便更新已发布帖", post.get("sync") is True)
check("官方帖含交通与主办", "광나루역" in post["body"] and "韩国电影演员协会" in post["body"] and "1670-0005" in post["body"])
check("官方信息卡存在且非海报文件", os.path.isfile(img) and os.path.getsize(img) > 20000)
check("信息卡不是提交的活动海报", os.path.getsize(img) != 2035880)

# 11月28日应落入首页速报 21 天窗口（与 daily_brief.events_for 相同规则）
d = datetime.date(2026, 11, 28)
s = datetime.date.fromisoformat(item["start"])
e = datetime.date.fromisoformat(item["end"])
check("11-28 的 21 天窗口能筛到本活动", e >= d and (s - d).days <= 21, (s - d).days)

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
