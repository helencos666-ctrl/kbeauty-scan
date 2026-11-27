"""平台精选第一批指南种子：条数、频道、幂等与待审核。python test/test_guides_seed.py"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import guides_seed as G

ok = fail = 0
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, str(info)[:400])

def read(name):
    return open(os.path.join(ROOT, name), encoding="utf-8").read()

posts = G.load_posts()
check("loads 20 posts", len(posts) == 20)
check("data file exists", os.path.isfile(G.data_path()) and G.data_path().endswith("guides_batch1.json"))

from collections import Counter
boards = Counter(p["board"] for p in posts)
check("boards are visa/news/daily/rent", set(boards) <= G.ALLOWED_BOARDS)
check("visa 4", boards["visa"] == 4)
check("daily 4", boards["daily"] == 4)
check("rent 4", boards["rent"] == 4)
check("news 8 including tax", boards["news"] == 8)
tax = [p for p in posts if p["channel"] == "购物退税"]
check("four tax posts", len(tax) == 4 and all(p["board"] == "news" and p["tag"] == "tax" for p in tax))
nontax = [p for p in posts if p["channel"] != "购物退税"]
check("non-tax posts have no tag", all(p["tag"] is None for p in nontax))
check("all posts valid", all(G.validate_post(p) is None for p in posts))
titles = [p["title"] for p in posts]
check("titles unique", len(titles) == len(set(titles)))
check("title and body length", all(1 <= len(p["title"]) <= 40 and 1 <= len(p["body"]) <= 3000 for p in posts))
text = "\n".join(p["title"] + "\n" + p["body"] for p in posts)
check("no wechat or qr", "微信" not in text and "二维码" not in text)
check("no 奋韩", "奋韩" not in text)
check("no 第一/唯一", "第一" not in text and "唯一" not in text)

merch = read("merch.py")
check("tax tag mapped to 退税", 'EXTRA_TAGS = {"news": (("tax", "退税", "🧾"),)}' in merch)
check("tag_name reads EXTRA_TAGS", "EXTRA_TAGS.get(board" in merch)

ret = read("retention.py")
check("official nick is 平台精选", 'OFFICIAL_NICK = "旅韩助手·平台精选"' in ret)
check("official nick length", 2 <= len("旅韩助手·平台精选") <= 16)
check("old nick kept as alias constant", 'OFFICIAL_NICK_OLD = "韩友圈小编"' in ret)
check("renames existing official account", "UPDATE c_users SET nickname=%s" in ret and "WHERE id=%s AND is_official" in ret)

src = read("guides_seed.py")
check("inserts as pending", "'pending'" in src)
check("does not approve", "/review" not in src and "status='published'" not in src and '"published"' not in src.replace("'pending'",""))
check("does not pin or feature", "pinned, featured, topic)" in src and "FALSE,FALSE,FALSE" in src)
check("idempotent by title", "title=%s" in src and "NOT deleted" in src)
check("logs through helper", "def _log(" in src and "inserted=" in src)
check("does not print secrets", "print(_db_url" not in src and "print(os.environ" not in src and "print(os.getenv" not in src)
check("does not call official-post", "official-post" not in src)

app = read("app.py")
check("app starts both seeds", "seed_lanjing" in app and "seed_guides" in app)
check("lanjing seed still runs", "for fn in (seed_lanjing, hide_shop_posts_v76, hide_shop_posts_v77, seed_guides, seed_official_daily)" in app and "_run_startup_seeds()" in app)

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
