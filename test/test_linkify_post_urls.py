"""帖子正文网址可点击：python test/test_linkify_post_urls.py"""
import os, sys
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

from community import _esc_html, linkify_escaped

src = "见 https://lvhanzhushou.com/event/seoul-movie-awards 。投票 https://sgmafans.com/ 。官网 http://kfaa.kr/"
html = linkify_escaped(_esc_html(src))
check("站内活动页做成 <a> 且不新开窗口",
      'href="https://lvhanzhushou.com/event/seoul-movie-awards">' in html
      and "target=" not in html.split("seoul-movie-awards")[1].split("投票")[0], html)
check("站外 https 链接新开窗口",
      'href="https://sgmafans.com/" target="_blank" rel="noopener noreferrer"' in html, html)
check("站外 http 链接同样可点",
      'href="http://kfaa.kr/" target="_blank" rel="noopener noreferrer"' in html, html)
check("句号留在链接外面", "> 。投票" in html, html)

# 先转义再链接，避免正文里的引号变成属性
raw = '点击 https://evil.example/" onclick="alert(1) 结束'
html = linkify_escaped(_esc_html(raw))
check("引号被转义，不会变成新属性", 'onclick="' not in html and "&quot;" in html, html)
check("尖括号被转义", _esc_html("<script>") == "&lt;script&gt;")

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
