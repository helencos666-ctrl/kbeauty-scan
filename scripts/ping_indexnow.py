#!/usr/bin/env python3
"""部署后向 IndexNow（api.indexnow.org）提交站点公开网址，便于 Bing 等更快发现更新。
默认读取同级目录的 indexnow.key 和 PUBLIC_BASE_URL / SITE_URL（缺省 https://lvhanzhushou.com）。
不会在导入时执行；直接运行本脚本才会发送请求。"""
import json, os, sys, urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_SITE = "https://lvhanzhushou.com"
INDEXNOW = "https://api.indexnow.org/indexnow"

# 与 seo.SITEMAP_PATHS 保持一致的固定页面；如需带上最新帖子，可先抓取 /sitemap.xml
PATHS = [
    "/", "/attractions", "/food", "/shopping", "/tips", "/beauty", "/faq",
    "/community", "/community/boards", "/community/rate", "/community/friends",
    "/rules", "/member",
]


def origin():
    return (os.environ.get("PUBLIC_BASE_URL") or os.environ.get("SITE_URL") or DEFAULT_SITE).rstrip("/")


def key():
    path = os.path.join(ROOT, "indexnow.key")
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def url_list(base):
    urls = [base + (p if p != "/" else "/")]
    # 再附上 sitemap，搜索引擎可从中展开全部公开页
    urls.append(base + "/sitemap.xml")
    return urls


def main():
    base = origin()
    k = key()
    payload = {
        "host": base.split("://", 1)[-1],
        "key": k,
        "keyLocation": f"{base}/{k}.txt",
        "urlList": url_list(base),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        INDEXNOW,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        print("IndexNow", resp.status, resp.read()[:200])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("IndexNow ping failed:", e, file=sys.stderr)
        sys.exit(1)
