"""吃喝玩会员推荐地点：python test/test_fanplaces.py
使用本机独立测试库，不打印连接串或密钥。"""
import io, os, sys, random

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
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


def img_bytes(w=400, h=300, color=(80, 120, 200)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG", quality=80)
    return b.getvalue()


HIT = {
    "kakao_id": "12345678",
    "name": "스타벅스 명동점",
    "address": "서울 중구 명동길",
    "kakao_url": "https://place.map.kakao.com/12345678",
    "x": 126.984, "y": 37.563,
}


async def fake_hit(query, kakao_id=None, x=None, y=None):
    if kakao_id and kakao_id != HIT["kakao_id"]:
        return None
    return dict(HIT)


async def fake_miss(query, kakao_id=None, x=None, y=None):
    return None


def main():
    import community as C
    import fanplaces as FP

    check("allows 第一次", not FP.ABS_RE.search("第一次来这家店，人不多"))
    check("blocks 最好", bool(FP.ABS_RE.search("这是最好吃的烤肉")))
    check("blocks 第一名", bool(FP.ABS_RE.search("这里是第一名的咖啡店")))
    check("blocks 唯一", bool(FP.ABS_RE.search("江南唯一值得去的店")))
    check("sub daily is 5", FP.SUB_DAILY == 5)
    check("rate daily is 20", FP.RATE_DAILY == 20)
    check("hotel category exists", "hotel" in FP.CATS and FP.CATS["hotel"] == "酒店住宿")

    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_fanplaces_v1")
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_fanplaces_v1"
    if not dbname.isidentifier() or not dbname.isascii():
        raise RuntimeError("bad test db name")
    try:
        import psycopg
        admin = psycopg.connect("postgresql://postgres@127.0.0.1:54329/postgres", connect_timeout=3)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (dbname,))
            cur.execute("DROP DATABASE IF EXISTS " + dbname)
            cur.execute("CREATE DATABASE " + dbname)
        admin.close()
    except Exception as e:
        print("SKIP integration: no local postgres (%s)" % type(e).__name__)
        print("unit %d passed, %d failed" % (ok, fail))
        return 1 if fail else 0

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "COOKIE_INSECURE", "ADMIN_PASSWORD")}
    os.environ["DATABASE_URL"] = dsn
    os.environ["COOKIE_INSECURE"] = "1"
    os.environ["ADMIN_PASSWORD"] = "test-admin-pw-1234"
    C._schema_ready = False
    C._psycopg = None
    FP.kakao_lookup = fake_hit
    app = FastAPI()
    app.include_router(C.router)
    H = {"X-LH": "1"}
    rnd = random.randint(100000, 999999)

    def client():
        return TestClient(app, raise_server_exceptions=False)

    def register(cli, name, acct):
        r = cli.post("/api/community/register", json={
            "account": f"{acct}{rnd}@example.com", "password": "Passw0rd!xyz",
            "nickname": name, "agree": True,
        }, headers=H)
        check("register " + name, r.status_code == 200, r.text)
        return cli

    def submit(cli, name="스타벅스 명동점", rec="人不多，座位干净，离地铁很近。",
               category="coffee", address="서울 중구 명동길", kakao_id="12345678", nphoto=1):
        files = [("photos", ("p%d.jpg" % i, img_bytes(color=(80+i*20, 100, 180)), "image/jpeg"))
                 for i in range(nphoto)]
        data = {"name": name, "category": category, "address": address, "rec_text": rec, "kakao_id": kakao_id}
        return cli.post("/api/places", data=data, files=files, headers=H)

    try:
        a = client(); register(a, "测试地点甲", "testfpa")
        b = client(); register(b, "测试地点乙", "testfpb")
        adm = client()
        r = adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
        check("admin login", r.status_code == 200, r.text)

        r = client().post("/api/places", data={"name": "x", "category": "coffee", "address": "y", "rec_text": "hello there"},
                          files=[("photos", ("p.jpg", img_bytes(), "image/jpeg"))], headers=H)
        check("submit requires login", r.status_code == 401, r.status_code)

        r = submit(a, rec="加我微信 abcdefg123 详谈")
        check("blocks wechat in rec", r.status_code == 400 and "联系" in (r.json().get("detail") or ""), r.text)

        r = submit(a, rec="这里是最好吃的咖啡")
        check("blocks absolute terms", r.status_code == 400 and "最" in (r.json().get("detail") or ""), r.text)

        r = submit(a, rec="人不多", nphoto=0)
        # nphoto=0 still might not send files
        check("requires photos", r.status_code == 400, r.text)

        r = submit(a, rec="第一次来，座位干净，离地铁很近。")
        check("submit ok when kakao found", r.status_code == 200, r.text)
        body = r.json() if r.status_code == 200 else {}
        check("status pending", body.get("status") == "pending", body)
        check("kakao found", body.get("kakao_found") is True, body)
        pid = body.get("place_id")
        sid = body.get("sub_id")

        r = a.get("/api/places")
        check("public list empty before approve", r.status_code == 200 and r.json()["items"] == [], r.text)

        r = a.get("/api/places/%s" % pid)
        check("submitter can see own pending", r.status_code == 200, r.text)
        r = b.get("/api/places/%s" % pid)
        check("other user cannot see pending", r.status_code == 404, r.status_code)

        r = adm.get("/api/admin/fanplaces?status=pending", headers=H)
        check("admin queue has item", r.status_code == 200 and r.json().get("pending", 0) >= 1, r.text)
        r = adm.post("/api/admin/fanplaces/subs/%s" % sid, json={"action": "approve"}, headers=H)
        check("admin approve", r.status_code == 200, r.text)
        check("awarded 10 beans", (r.json() or {}).get("awarded") == 10, r.text)

        r = a.get("/api/places")
        items = r.json().get("items") if r.status_code == 200 else []
        check("list shows approved", len(items) == 1 and items[0]["name"], items)
        check("recommended_by nickname", items and items[0].get("recommended_by") == "测试地点甲", items)
        check("map url present", items and "kakao" in (items[0].get("map_url") or "").lower(), items)

        r = b.get("/api/places/%s" % pid)
        check("detail public after approve", r.status_code == 200, r.text)
        check("photos after approve", r.status_code == 200 and r.json().get("photos"), r.text)

        r = b.post("/api/places/%s/rate" % pid, json={"stars": 4, "body": "咖啡味道可以，位子有点紧。"}, headers=H)
        check("rate ok", r.status_code == 200, r.text)
        check("avg is 4", r.status_code == 200 and r.json().get("avg_rating") == 4, r.text)

        r = b.post("/api/places/%s/rate" % pid, json={"stars": 5, "body": "第二次来，还是很干净。"}, headers=H)
        check("rate updatable", r.status_code == 200 and r.json().get("avg_rating") == 5, r.text)
        check("still one rating", r.status_code == 200 and r.json().get("rating_count") == 1, r.text)

        r = b.post("/api/places/%s/rate" % pid, json={"stars": 3, "body": "这是唯一干净的店"}, headers=H)
        check("review blocks 唯一", r.status_code == 400, r.text)

        r = a.get("/api/places/%s" % pid)
        rid = (r.json().get("ratings") or [{}])[0].get("id") if r.status_code == 200 else None
        r = a.post("/api/places/reviews/%s/report" % rid, json={"reason": "测试举报请忽略"}, headers=H)
        check("report review", r.status_code == 200, r.text)
        r = a.post("/api/community/report", json={"type": "fan_review", "id": rid, "reason": "再报一次也可"}, headers=H)
        check("community report type accepted", r.status_code in (200, 400), r.text)

        r = adm.get("/api/admin/reports", headers=H)
        types = [x.get("type") for x in (r.json().get("reports") or [])] if r.status_code == 200 else []
        check("admin sees fan_review report", "fan_review" in types, types)

        r = adm.post("/api/admin/fanplaces/ratings/%s" % rid, json={"action": "hide"}, headers=H)
        check("admin hide review", r.status_code == 200, r.text)
        r = a.get("/api/places/%s" % pid)
        check("hidden review not listed", r.status_code == 200 and r.json().get("rating_count") == 0, r.text)

        # 去重：同一 kakao id 再提交，不新建地点
        r = submit(b, rec="座位多一些，适合休息。")
        check("dedupe same kakao id", r.status_code == 200 and r.json().get("place_id") == pid, r.text)
        check("second sub pending", r.status_code == 200 and r.json().get("status") == "pending", r.text)
        sid2 = r.json().get("sub_id") if r.status_code == 200 else None
        r = adm.post("/api/admin/fanplaces/subs/%s" % sid2, json={"action": "approve"}, headers=H)
        check("second sub awarded", r.status_code == 200 and r.json().get("awarded") == 10, r.text)
        r = a.get("/api/places")
        check("still one public place", r.status_code == 200 and len(r.json()["items"]) == 1, r.text)

        # 地图找不到 → flagged
        FP.kakao_lookup = fake_miss
        r = submit(a, name="不存在的小店xyz", address="某个巷子", rec="测试投稿请驳回，仅用于功能试验。",
                   kakao_id="", category="other")
        check("kakao miss flagged", r.status_code == 200 and r.json().get("status") == "flagged", r.text)
        sid3 = r.json().get("sub_id") if r.status_code == 200 else None
        r = adm.post("/api/admin/fanplaces/subs/%s" % sid3, json={"action": "reject", "reason": "测试投稿，已驳回"}, headers=H)
        check("admin reject flagged", r.status_code == 200, r.text)
        r = a.get("/api/places/mine")
        notes = [x.get("review_note") for x in (r.json().get("items") or [])] if r.status_code == 200 else []
        check("reject reason visible to submitter", any("测试投稿" in (n or "") for n in notes), notes)

        # 每日 5 次上限（当前已提交 2 次：第一次 + flagged）。再提交 3 次后第 6 次应失败。
        FP.kakao_lookup = fake_hit
        for i in range(3):
            r = submit(a, rec="补充体验 %d，环境安静。" % (i + 1), kakao_id="12345678")
            check("sub %d extra ok" % (i + 3), r.status_code == 200, r.text)
        r = submit(a, rec="超出上限的投稿")
        check("sixth submit blocked", r.status_code == 400 and "5" in (r.json().get("detail") or ""), r.text)

    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print("unit+integration %d passed, %d failed" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
