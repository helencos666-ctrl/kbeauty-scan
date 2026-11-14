"""生成 static/stations.json：韩国铁路/地铁站韩文站名 → [经度, 纬度]（WGS84）
数据来源：Wikidata（CC0 公共领域），免费、无需密钥。用法：python scripts/build_stations.py"""
import json, math, os, re, urllib.parse, urllib.request
from collections import defaultdict

Q = """SELECT ?item ?ko ?coord WHERE {
  VALUES ?type { wd:Q928830 wd:Q55488 wd:Q4663385 wd:Q1793804 }
  ?item wdt:P31 ?type; wdt:P17 wd:Q884; wdt:P625 ?coord.
  ?item rdfs:label ?ko FILTER(LANG(?ko)="ko")
}"""
url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": Q})
req = urllib.request.Request(url, headers={"Accept": "application/sparql-results+json",
                                           "User-Agent": "lvhan-guide-build/1.0 (static dataset build)"})
rows = json.load(urllib.request.urlopen(req, timeout=120))["results"]["bindings"]

pts = defaultdict(list)
for r in rows:
    name = re.sub(r"\s*\(.*?\)\s*", "", r["ko"]["value"]).strip()
    if not name.endswith("역"):
        continue
    m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", r["coord"]["value"])
    if not m:
        continue
    lng, lat = float(m.group(1)), float(m.group(2))
    if not (124 < lng < 132 and 33 < lat < 39):
        continue
    pts[name].append((lng, lat))

def capital(p):  # 首都圈优先（同名站在不同城市时）
    return 126.5 < p[0] < 127.5 and 37.1 < p[1] < 37.95

out = {}
for name, ps in pts.items():
    ps = sorted(set(ps), key=lambda p: (not capital(p), p))
    base = ps[0]
    near = [p for p in ps if math.hypot(p[0] - base[0], p[1] - base[1]) < 0.01]
    lng = sum(p[0] for p in near) / len(near); lat = sum(p[1] for p in near) / len(near)
    out[name] = [round(lng, 5), round(lat, 5)]

# Wikidata 暂缺坐标的站，按公开资料补充
EXTRA = {"건대입구역": [127.07023, 37.54069], "경성대부경대역": [129.10058, 35.13781]}
for k, v in EXTRA.items():
    out.setdefault(k, v)

dst = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "stations.json")
with open(dst, "w", encoding="utf-8") as f:
    json.dump({"source": "Wikidata (CC0)", "count": len(out), "stations": dict(sorted(out.items()))}, f, ensure_ascii=False, separators=(",", ":"))
print(len(out), "stations ->", dst)
