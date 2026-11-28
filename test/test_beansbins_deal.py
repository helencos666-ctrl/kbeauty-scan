"""BEANSBINS 明洞店到店优惠：python test/test_beansbins_deal.py"""
import json, os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
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

food = json.loads(read("static", "food.json"))
cfg = json.loads(read("static", "opening-promos.json"))
html = read("static", "food.html")
promo = next(i for i in cfg["items"] if i["id"] == "beansbins-11")
item = next(i for i in food["items"] if i["id"] == "beansbins-myeongdong")
coupon = os.path.join(ROOT, "static", "food-photos", "beansbins-myeongdong-coupon.jpg")

check("美食页有明洞店条目", item["name_ko"] == "빈스빈스 명동사보이호텔점")
check("地址为萨沃伊酒店 2 层", "명동8나길 10" in item["address"] and "사보이호텔 2층" in item["address"])
check("电话与券面一致", item.get("phone") == "02-3789-7023")
check("营业时间含 08:00–23:00 并以店内为准", "08:00–23:00" in item["hours"] and "以店内当日公示为准" in item["hours"])
check("优惠含特调送美式与 prettyskin", "特调" in item["offer"] and "美式" in item["offer"] and "prettyskin" in item["offer"])
check("优惠不含无限次使用", "无限次" not in json.dumps(item, ensure_ascii=False))
check("优惠注明以店内为准", "以店内为准" in item["offer"])
check("优惠券图片存在", os.path.isfile(coupon) and os.path.getsize(coupon) > 10000)
check("条目引用优惠券图片", item.get("coupon", "").endswith("beansbins-myeongdong-coupon.jpg"))
check("未用生成的假店景替换已有实拍", all("beansbins-myeongdong-" in p for p in item["photos"]))
check("开业活动指向美食页锚点", promo["href"] == "/food#beansbins-myeongdong" and promo.get("enabled") is True)
check("开业活动口令为小写 prettyskin", "prettyskin" in json.dumps(promo, ensure_ascii=False) and "PrettySkin" not in json.dumps(promo, ensure_ascii=False))
check("开业活动不含无限次使用", "无限次" not in json.dumps(promo, ensure_ascii=False))
check("美食页会渲染到店优惠、电话和优惠券", "到店优惠" in html and "it.phone" in html and "it.coupon" in html and "class=\"coupon\"" in html)

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
