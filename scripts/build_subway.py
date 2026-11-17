"""生成 static/subway.json（首都圈地铁线路图，用于站内路线计算）。

数据来源（均为开放数据，须保留署名）：
- 线路、停靠站顺序、线路颜色、站名（韩/英/部分中文）：OpenStreetMap 公交线路关系（route relations），
  © OpenStreetMap contributors，ODbL 1.0 许可 https://www.openstreetmap.org/copyright
  通过 Overpass API 获取，查询语句见 scripts/subway_overpass.ql
- 站名中文/英文补充：Wikidata（CC0）
- 中文别名：static/index.html 中原有的旅游常用站名别名表（人工整理）
运行：python scripts/build_subway.py  （需要 pypinyin、opencc-python-reimplemented；会联网下载数据）
注意：不含急行/直通/特急列车（只用各站停车线路计算站数），也不含 GTX、磁悬浮。
"""
import json, math, os, re, sys, urllib.request, urllib.parse, datetime
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.environ.get("SUBWAY_CACHE", "/tmp/sub")
UA = {"User-Agent": "lvhanzhushou-build/1.0"}

def fetch(path, url, data=None):
    p = os.path.join(CACHE, path)
    if os.path.exists(p):
        return json.load(open(p))
    os.makedirs(CACHE, exist_ok=True)
    req = urllib.request.Request(url, data=data, headers=UA)
    raw = urllib.request.urlopen(req, timeout=300).read()
    open(p, "wb").write(raw)
    return json.loads(raw)

osm = fetch("full.json", "https://overpass-api.de/api/interpreter",
            urllib.parse.urlencode({"data": open(os.path.join(HERE, "subway_overpass.ql")).read()}).encode())
WDQ = ('SELECT ?ko ?zh ?zhh ?zhcn ?en WHERE { ?s wdt:P17 wd:Q884; wdt:P31/wdt:P279* wd:Q55488; rdfs:label ?ko FILTER(LANG(?ko)="ko") '
       'OPTIONAL{?s rdfs:label ?zh FILTER(LANG(?zh)="zh")} OPTIONAL{?s rdfs:label ?zhh FILTER(LANG(?zhh)="zh-hans")} '
       'OPTIONAL{?s rdfs:label ?zhcn FILTER(LANG(?zhcn)="zh-cn")} OPTIONAL{?s rdfs:label ?en FILTER(LANG(?en)="en")} }')
wd = fetch("wd.json", "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(WDQ))

from opencc import OpenCC
from pypinyin import lazy_pinyin
t2s = OpenCC("t2s").convert
s2t = OpenCC("s2t").convert

LINES = {  # OSM ref -> (中文名, 韩文名)
    "1": ("1号线", "1호선"), "2": ("2号线", "2호선"), "3": ("3号线", "3호선"), "4": ("4号线", "4호선"),
    "5": ("5号线", "5호선"), "6": ("6号线", "6호선"), "7": ("7号线", "7호선"), "8": ("8号线", "8호선"),
    "9": ("9号线", "9호선"), "공항철도": ("机场铁路", "공항철도"), "AREX": ("机场铁路", "공항철도"),
    "경의·중앙": ("京义·中央线", "경의·중앙선"), "수인·분당": ("水仁·盆唐线", "수인·분당선"),
    "신분당": ("新盆唐线", "신분당선"), "경춘": ("京春线", "경춘선"), "경강": ("京江线", "경강선"),
    "서해": ("西海线", "서해선"), "W": ("牛耳新设线", "우이신설선"), "Silim": ("新林线", "신림선"),
    "김포 골드라인": ("金浦黄金线", "김포골드라인"), "인천1": ("仁川1号线", "인천1호선"),
    "I2": ("仁川2号线", "인천2호선"), "용인": ("龙仁轻轨", "용인경전철"), "U": ("议政府轻轨", "의정부경전철"),
}
CANON = {"AREX": "공항철도"}
SKIP = re.compile(r"급행|특급|직통|express|ITX|차량기지", re.I)
# 常用景点 → 最近的地铁站（人工整理，只收位置明确的；用于搜索，不代表出口）
LANDMARKS = {
    "종로5가": ["广藏市场", "广藏", "gwangjangmarket"], "안국": ["北村", "北村韩屋村", "仁寺洞", "昌德宫", "bukchon", "insadong"],
    "종로3가": ["益善洞", "ikseondong"], "광화문": ["光化门广场", "世宗文化会馆", "清溪川"], "경복궁": ["青瓦台", "国立古宫博物馆"],
    "회현": ["南大门市场", "新世界百货", "namdaemun"], "명동": ["南山", "南山塔", "首尔塔", "明洞大教堂", "nseoultower", "namsan"],
    "을지로입구": ["乐天百货总店", "乐天免税店明洞"], "삼성": ["COEX", "星空图书馆", "coexmall"], "잠실": ["乐天世界", "乐天塔", "乐天世界塔", "石村湖", "lotteworld"],
    "여의나루": ["汝矣岛汉江公园", "汉江公园"], "서울숲": ["首尔林公园"], "이대": ["梨花女子大学", "梨大"], "신촌": ["延世大学"],
    "삼각지": ["战争纪念馆"], "이촌": ["国立中央博物馆"], "서울대입구": ["首尔大学"], "망원": ["望远市场"],
    "동대문역사문화공원": ["DDP", "东大门设计广场", "东大门市场"], "신사": ["林荫道", "garosugil"], "가평": ["南怡岛"],
    "전대·에버랜드": ["爱宝乐园", "everland"], "인천공항1터미널": ["仁川机场", "仁川国际机场", "icn"], "김포공항": ["gmp"],
    "고속터미널": ["高速巴士客运站", "高速客运站", "expressbusterminal"], "서울": ["首尔站", "seoulstation"],
}
SPLIT_BY_LINE = {"신촌": {"경의·중앙"}}   # 同名但不是同一个换乘站
MERGE_M = 1500

def hav(a, b):
    R = 6371000; la1, lo1, la2, lo2 = map(math.radians, (a[1], a[0], b[1], b[0]))
    x = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2*R*math.asin(math.sqrt(x))

def nm(t):
    n = (t.get("name:ko") or t.get("name") or "").strip()
    n = re.sub(r"\s*\(.*?\)\s*", "", n)
    return re.sub(r"역$", "", n)

nodes = {e["id"]: e for e in osm["elements"] if e["type"] == "node"}
rels = [e for e in osm["elements"] if e["type"] == "relation"]
st = {}
# 停靠点没写站名时（如机场铁路数码媒体城站），借用 400 米内最近的有名称停靠点
_named = [(nm(n.get("tags", {})), (n["lon"], n["lat"])) for n in nodes.values() if nm(n.get("tags", {}))]
def nearest_name(node):
    p = (node["lon"], node["lat"]); best = (400, None)
    for n, q in _named:
        dd = hav(p, q)
        if dd < best[0]: best = (dd, n)
    return best[1]
def station(node, line):
    t = node.get("tags", {}); n = nm(t) or nearest_name(node)
    if not n: return None
    p = (round(node["lon"], 5), round(node["lat"], 5))
    tag = n + "@" + line if line in SPLIT_BY_LINE.get(n, ()) else n
    for k, s in st.items():
        if s["tag"] == tag and hav(s["p"], p) < MERGE_M: return k
    k = tag if tag not in st else f"{tag}#{sum(1 for s in st.values() if s['tag']==tag)+1}"
    st[k] = {"tag": tag, "ko": n, "p": p, "zh": "", "en": "", "lines": set()}
    return k

edges = {}
for r in rels:
    t = r["tags"]; name = t.get("name", "")
    ref = CANON.get(t.get("ref", ""), t.get("ref", ""))
    if SKIP.search(name) or ref not in LINES: continue
    seq = []
    for m in r["members"]:
        if m["type"] == "node" and m["role"].startswith("stop") and m["ref"] in nodes:
            k = station(nodes[m["ref"]], ref)
            if not k: continue
            nt = nodes[m["ref"]].get("tags", {})
            st[k]["zh"] = st[k]["zh"] or nt.get("name:zh-Hans") or nt.get("name:zh") or ""
            st[k]["en"] = st[k]["en"] or nt.get("name:en") or ""
            if not seq or seq[-1] != k: seq.append(k)
    loop = ""
    if name.endswith("내선순환"): loop = "内环"
    elif name.endswith("외선순환"): loop = "外环"
    for a, b in zip(seq, seq[1:]):
        if a == b: continue
        e = edges.setdefault((a, b, ref), {"loop": ""})
        if loop: e["loop"] = loop
        st[a]["lines"].add(ref); st[b]["lines"].add(ref)

# 中文名：原有别名表 > Wikidata > OSM
alias = json.load(open(os.path.join(CACHE, "aliases.json")))
alias_by_ko = {re.sub(r"역$", "", ko): als for ko, als in alias}
wdzh, wden = {}, {}
for b in wd["results"]["bindings"]:
    ko = re.sub(r"\s*\(.*?\)\s*", "", b["ko"]["value"]); ko = re.sub(r"역$", "", ko)
    z = (b.get("zhh") or b.get("zhcn") or b.get("zh") or {}).get("value")
    if z and ko not in wdzh: wdzh[ko] = re.sub(r"(站|駅)$", "", t2s(z))
    if b.get("en") and ko not in wden: wden[ko] = re.sub(r"\s+Station$", "", b["en"]["value"])
CJK = re.compile(r"[\u4e00-\u9fff]")
out_st = []
idx = {}
for k, s in sorted(st.items(), key=lambda x: x[0]):
    if not s["lines"]: continue
    ko = s["ko"]
    als = [a for a in alias_by_ko.get(ko, []) if a]
    zh_alias = [a for a in als if CJK.search(a)]
    zh = (zh_alias[0] if zh_alias else "") or wdzh.get(ko) or t2s(re.sub(r"[（(].*?[)）]|站$", "", s["zh"]))
    zh = re.sub(r"站$", "", zh).strip()
    en = re.sub(r"\s+Station$", "", s["en"] or wden.get(ko, "")).strip()
    keys = set(als) | {zh, en, ko} | set(LANDMARKS.get(ko, []) if "#" not in k and "@" not in k else [])
    for z in [zh] + zh_alias:
        if z and CJK.search(z):
            py = lazy_pinyin(z); keys.add("".join(py)); keys.add("".join(w[0] for w in py if w))
    keys |= {s2t(x) for x in list(keys) if CJK.search(x)}
    keys = sorted({re.sub(r"[\s.\-'’·]", "", x.lower()) for x in keys if x})
    idx[k] = len(out_st)
    out_st.append([ko, zh, en, s["p"][0], s["p"][1], sorted(s["lines"]), keys])
line_ids = sorted({e[2] for e in edges})
colour = {}
for r in rels:
    ref = CANON.get(r["tags"].get("ref", ""), r["tags"].get("ref", ""))
    if ref in LINES and r["tags"].get("colour", "").startswith("#") and ref not in colour:
        colour[ref] = r["tags"]["colour"]
out = {
    "source": "© OpenStreetMap contributors (ODbL 1.0) via Overpass API; 中文名补充 Wikidata (CC0)",
    "built": datetime.date.today().isoformat(),
    "lines": [[l, LINES[l][0], LINES[l][1], colour.get(l, "#888888")] for l in line_ids],
    "st": out_st,
    "e": [[idx[a], idx[b], line_ids.index(l), v["loop"]] for (a, b, l), v in edges.items() if a in idx and b in idx],
}
json.dump(out, open(os.path.join(ROOT, "static", "subway.json"), "w"), ensure_ascii=False, separators=(",", ":"))
print("stations", len(out_st), "edges", len(out["e"]), "lines", line_ids)
print("no zh:", sum(1 for s in out_st if not s[1]))
# 合并检查
far = [(s[0], s[5]) for s in out_st if "#" in s[0]]
print("dups:", far)
