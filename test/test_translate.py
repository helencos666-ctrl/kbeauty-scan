"""中韩翻译单元测试：python test/test_translate.py（不访问外网，不打印密钥）"""
import os, sys, asyncio, json
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


def main():
    import translate as T

    check("下一站去 is itinerary, not next station",
          T.pre_normalize_zh("下一站去弘大，去弘大怎么走呢?") == "接下来要去弘大，去弘大怎么走呢?")
    check("下一站是 stays station",
          "下一站是弘大" == T.pre_normalize_zh("下一站是弘大"))
    check("place hint 弘大", "弘大=홍대" in T.place_hints("去弘大怎么走", "zh2ko"))
    check("apply_place_ko replaces leftover 弘大",
          "홍대" in T.apply_place_ko("去弘大 어떻게 가요?", "去弘大怎么走") and "弘大" not in T.apply_place_ko("去弘大 어떻게 가요?", "去弘大怎么走"))

    rom = T.romanize("홍대")
    check("romanize 홍대", rom.replace(" ", "") == "hongdae", rom)
    rom = T.romanize("안녕하세요")
    check("romanize 안녕하세요 starts with annyeong", rom.startswith("annyeong"), rom)
    rom = T.romanize("강남역 10번 출구")
    check("romanize keeps numbers", "10" in rom and "gangnam" in rom.replace(" ", ""), rom)

    obj = T.parse_obj('```json\n{"ko":"안녕하세요"}\n```')
    check("parse fenced json", obj.get("ko") == "안녕하세요", obj)
    swapped = T._pick_ko({"ko": "接下来要去弘大", "roman": "다음에 홍대에 가려고 하는데요"}, "")
    check("swap when ko is Chinese", "홍대" in swapped, swapped)
    zh = T._pick_zh({"zh": "到弘大怎么走？"}, "")
    check("pick zh", "弘大" in zh, zh)

    # cache / no network if empty creds
    old = {k: os.environ.pop(k, None) for k in ("CF_ACCOUNT_ID", "CF_AI_TOKEN")}
    try:
        r = asyncio.run(T.translate_text("下一站去弘大", "zh2ko"))
        check("no creds returns empty ko", r.get("ko") == "" and r.get("engine") == "", r)
    finally:
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v

    # inject a cache entry
    src = T.pre_normalize_zh("下一站去弘大，去弘大怎么走呢?")
    T._tc_put((T.CACHE_VER, "zh2ko", "subway", src, False), {
        "ko": "이번에 홍대에 가려고 하는데, 홍대까지 어떻게 가요?",
        "roman": "ibeone hongdaee garyeogo haneunde, hongdaekkaji eotteoke gayo?",
        "engine": "llm",
    })
    hit = T.lookup_cached("下一站去弘大，去弘大怎么走呢?", "zh2ko", "subway")
    check("cache hit after colloquial normalize", bool(hit and "홍대" in hit.get("ko", "")), hit)

    # mocked LLM
    async def fake_llm(account, token, messages, timeout):
        return json.dumps({"ko": "이 근처에 약국이 있나요?"}, ensure_ascii=False)

    async def boom(*a, **k):
        raise AssertionError("m2m should not be called")

    T._BACK_WARM = False
    T._llm, T._m2m, saved = fake_llm, boom, (T._llm, T._m2m)
    os.environ["CF_ACCOUNT_ID"] = "x" * 32
    os.environ["CF_AI_TOKEN"] = "y" * 20
    try:
        r = asyncio.run(T.translate_text("这附近有药店吗", "zh2ko"))
        check("mocked llm ko", r.get("ko") == "이 근처에 약국이 있나요?", r)
        check("mocked engine llm", r.get("engine") == "llm", r)
        rom=(r.get("roman") or "")
        check("local roman has yakgug", "yakgug" in rom, rom)
    finally:
        T._llm, T._m2m = saved
        os.environ.pop("CF_ACCOUNT_ID", None)
        os.environ.pop("CF_AI_TOKEN", None)

    print("unit %d passed, %d failed" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
