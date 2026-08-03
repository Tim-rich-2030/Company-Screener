# -*- coding: utf-8 -*-
"""
첫 화면을 채우는 세 가지 — 조건 필터 · 시장 지도 · 뉴스 — 테스트.

셋 다 바깥에서 받아오는 것이 있지만, 받아오는 부분과 계산·파싱을 갈라 두고
여기서는 계산·파싱만 손으로 검산한다. 네트워크가 없어도 돌아야 한다.

실행:  python tests/test_board.py
"""
import os
import sys
import json
import tempfile
import datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from screener import screen
import market_tree as mt
import market_news as mn
import market_flags
import market_calendar as mc
import market_macro as mm
import market_theme as mth
import market_strong as mst
import market_board as mb
import market_headline as mh


def eq(got, want, what):
    assert got == want, f"{what}: {got!r} != {want!r}"


# =============================================================================
# 조건 필터
# =============================================================================

def _co(name, currency="KRW", halted=None, **metrics):
    """지표는 분기별 목록이다. 최신 분기가 [0] 이므로 값 하나만 넣어도 된다."""
    return {"name": name, "currency": currency, "halted": halted,
            "quarters": ["2026Q1"],
            "metrics": {k: [v] for k, v in metrics.items()}}


PASSING = dict(**{"PBR": 0.5, "PER": 5.0, "ROE(%)": 10.0,
                  "부채비율(%)": 100.0, "영업흑자 분기": 4.0, "PBR/ROE": 0.05})


def test_screen_all_conditions_must_hold():
    data = {
        "000001": _co("통과", **PASSING),
        "000002": _co("비싸다", **{**PASSING, "PBR": 1.5}),
        "000003": _co("적자", **{**PASSING, "PER": -3.0}),
        "000004": _co("수익성없음", **{**PASSING, "ROE(%)": 2.0}),
        "000005": _co("빚많음", **{**PASSING, "부채비율(%)": 350.0}),
        "000006": _co("흑자부족", **{**PASSING, "영업흑자 분기": 2.0}),
    }
    out = screen.build(data)
    eq([h["name"] for h in out["items"]], ["통과"], "조건 하나만 어겨도 탈락")
    eq(out["screened"], 6, "본 종목 수")
    print("test_screen_all_conditions_must_hold: OK")


def test_screen_drops_unknown_values():
    """
    값이 없는 종목은 통과시키지 않는다.

    비어 있는 것을 '조건에 걸리지 않았다'로 읽으면 조건을 건 의미가 없다 —
    모르는 종목이 통과 목록에 섞여 들어간다.
    """
    d = {"000001": _co("PER없음", **{**PASSING, "PER": None})}
    eq(screen.build(d)["items"], [], "값이 없으면 탈락")
    d2 = {"000001": _co("지표자체없음")}
    eq(screen.build(d2)["items"], [], "지표가 아예 없어도 탈락")
    print("test_screen_drops_unknown_values: OK")


def test_screen_excludes_foreign_currency_and_halted():
    """
    외화 보고 종목과 거래정지 의심 종목은 뺀다.

    외화 재무제표에 원화 시가총액을 나누면 PBR·PER 이 숫자만 그럴듯하고 뜻이
    없다. 거래정지는 가격 자체가 멈춰 있어 밸류 지표가 옛날 값이다.
    """
    data = {
        "000001": _co("정상", **PASSING),
        "000002": _co("달러보고", currency="USD", **PASSING),
        "000003": _co("거래정지", halted=["2026Q1", "2025Q4"], **PASSING),
    }
    eq([h["name"] for h in screen.build(data)["items"]], ["정상"], "제외 대상")
    print("test_screen_excludes_foreign_currency_and_halted: OK")


def test_screen_ranks_by_pbr_over_roe_and_states_the_rule():
    data = {
        "000001": _co("싼편", **{**PASSING, "PBR/ROE": 0.09}),
        "000002": _co("더싼편", **{**PASSING, "PBR/ROE": 0.02}),
        "000003": _co("순위없음", **{**PASSING, "PBR/ROE": None}),
    }
    out = screen.build(data)
    eq([h["name"] for h in out["items"]], ["더싼편", "싼편", "순위없음"],
       "낮은 PBR/ROE 부터, 값 없는 것은 맨 뒤")
    # 화면에 조건이 그대로 나가는지. 어떤 자로 잰 목록인지 보여야 한다.
    for key, _, _ in screen.RULES:
        assert key in out["rule"], f"조건 문구에 {key} 가 빠졌다: {out['rule']}"
    print("test_screen_ranks_by_pbr_over_roe_and_states_the_rule: OK")


def _hist(tmp, day, *codes):
    """그날 걸린 종목을 기록하고 changed 를 돌려준다."""
    p = {"items": [{"code": c, "name": "이름" + c} for c in codes]}
    return screen.record(p, day, tmp)["changed"]


def test_picks_first_run_says_it_has_nothing_to_compare():
    """
    견줄 기록이 없는 것과 '안 바뀐 것'은 다른 말이다.

    첫 실행에서 '어제와 같습니다'라고 적으면 거짓말이 된다. 그래서 None 을
    돌려주고, 화면은 그 경우 비교 문구 대신 없다고 적는다.
    """
    d = tempfile.mkdtemp()
    eq(_hist(os.path.join(d, "h.json"), "20260801", "1", "2"), None,
       "첫 실행은 견줄 것이 없다")
    print("test_picks_first_run_says_it_has_nothing_to_compare: OK")


def test_picks_diff_against_the_previous_collection_day():
    d = os.path.join(tempfile.mkdtemp(), "h.json")
    _hist(d, "20260801", "1", "2")
    c = _hist(d, "20260802", "2", "3")
    eq(c["since"], "20260801", "견준 날")
    eq([x["code"] for x in c["new"]], ["3"], "새로 들어온 것")
    eq([x["code"] for x in c["gone"]], ["1"], "빠진 것")
    eq(_hist(d, "20260803", "2", "3")["new"], [], "그대로면 빈 목록")
    print("test_picks_diff_against_the_previous_collection_day: OK")


def test_picks_rerun_on_the_same_day_compares_with_the_day_before():
    """
    하루에 두 번 돌 수 있다. 그때 오늘 것끼리 견주면 아침에 들어온 종목이
    오후 실행에서 조용히 사라진다. 같은 날이면 그 **전날**과 견딘다.
    """
    d = os.path.join(tempfile.mkdtemp(), "h.json")
    _hist(d, "20260801", "1", "2")
    _hist(d, "20260802", "2", "3")
    c = _hist(d, "20260802", "2", "3", "4")       # 같은 날 두 번째
    eq(c["since"], "20260801", "전날과 견준다")
    eq(sorted(x["code"] for x in c["new"]), ["3", "4"], "아침 것도 남아 있다")
    print("test_picks_rerun_on_the_same_day_compares_with_the_day_before: OK")


def test_picks_history_survives_a_broken_file_and_stays_bounded():
    d = os.path.join(tempfile.mkdtemp(), "h.json")
    with open(d, "w", encoding="utf-8") as f:
        f.write("{망가진 파일")
    eq(_hist(d, "20260801", "1"), None, "깨진 기록은 없는 것과 같이 다룬다")
    for i in range(screen.KEEP_DAYS + 5):
        _hist(d, "2026%04d" % (1000 + i), "1")
    with open(d, encoding="utf-8") as f:
        eq(len(json.load(f)["days"]), screen.KEEP_DAYS, "기록이 무한정 늘지 않는다")
    print("test_picks_history_survives_a_broken_file_and_stays_bounded: OK")


# =============================================================================
# 시장 지도
# =============================================================================

def test_sector_change_is_cap_weighted():
    """
    업종 등락률은 시총 가중이어야 한다.

    단순 평균이면 시총 1000억짜리 한 종목이 300조짜리와 같은 무게가 되어
    업종 전체를 끌고 간다.
    """
    items = [
        {"code": "1", "name": "큰놈", "market": "KOSPI", "sector": "화학",
         "chg": 1.0, "cap": 300_000_000_000_000, "close": 100},
        {"code": "2", "name": "작은놈", "market": "KOSPI", "sector": "화학",
         "chg": -9.0, "cap": 100_000_000_000, "close": 100},
    ]
    got = mt.sectors_of(items)[0]
    want = round((300_000_000_000_000 * 1.0 + 100_000_000_000 * -9.0)
                 / 300_100_000_000_000, 2)
    eq(got["chg"], want, "시총 가중 평균")
    assert got["chg"] > 0.9, f"단순 평균이면 -4.0 이 된다: {got['chg']}"
    eq((got["up"], got["down"], got["n"]), (1, 1, 2), "오른/내린 종목 수")
    print("test_sector_change_is_cap_weighted: OK")


def test_tiles_are_cut_per_market_but_aggregates_are_not():
    """
    타일은 시장별 시총 상위만 그리지만, 업종 집계와 상승/하락 종목 수는
    전 종목으로 낸다. 자른 뒤에 집계하면 '상승 2332' 가 대형주만의 이야기가 된다.
    """
    items = [{"code": str(i), "name": f"종목{i}",
              "market": "코스피" if i < 4 else "코스닥",
              "sector": "화학" if i < 3 else "은행",
              "chg": 1.0 if i % 2 else -1.0,
              "cap": (10 - i) * 1_000_000_000_000, "close": 5000}
             for i in range(6)]
    raw = {"date": "20260731", "source": "pykrx", "items": items,
           "seen": 100, "cut": {"관리종목": 3}, "flags": {"관리종목": 3}}
    out = mt.build(raw, top_n=2)
    eq(out["breadth"], {"up": 3, "down": 3, "flat": 0, "total": 6},
       "상승/하락 집계는 전 종목")
    eq(len(out["sectors"]), 2, "업종도 전 종목 기준")
    eq(out["markets"]["코스피"]["breadth"]["total"], 4, "코스피 전 종목 수")
    eq([t["name"] for t in out["markets"]["코스피"]["items"]], ["종목1", "종목0"],
       "시장별로 시총 상위 2개를 뽑은 뒤 상승순")
    eq(out["universe"]["seen"], 100, "제외 전 종목 수가 남는다")
    eq(out["universe"]["kept"], 6, "남은 종목 수")
    print("test_tiles_are_cut_per_market_but_aggregates_are_not: OK")


def test_collect_excludes_and_counts_why():
    """
    제외한 것은 세어서 남긴다. 조용히 빼면 지도가 시장 전체로 읽힌다.
    """
    class Row(dict):
        def get(self, k, d=None): return dict.get(self, k, d)

    class DF:
        def __init__(self, rows): self.rows = rows; self.empty = not rows
        def __contains__(self, k): return k == "종가"
        def __len__(self): return len(self.rows)
        def __getitem__(self, k):
            class Col:
                def __init__(s, v): s.v = v
                def __gt__(s, n): return Col([x > n for x in s.v])
                def any(s): return any(s.v)
            return Col([r["종가"] for r in self.rows])
        def iterrows(self):
            return iter([(r["code"], Row(r)) for r in self.rows])

    def row(code, close, cap=1e12, chg=1.0, sec="화학"):
        return {"code": code, "종목명": "이름" + code, "업종명": sec,
                "종가": close, "등락률": chg, "시가총액": cap}

    kospi = DF([row("000001", 5000), row("000002", 900),      # 동전주
                row("000003", 5000), row("000004", 5000)])    # 관리, 환기
    kosdaq = DF([row("100001", 3000)])

    class Stock:
        def get_market_sector_classifications(self, date, market):
            return kospi if market == "KOSPI" else kosdaq

    orig_stock, orig_flags = mt._stock, market_flags.collect
    mt._stock = lambda: Stock()
    market_flags.collect = lambda: {"관리종목": {"000003"},
                                    "투자주의환기종목": {"000004"},
                                    "source": {"관리종목": 1, "투자주의환기종목": 1}}
    try:
        raw = mt.collect("20260731", exclude_under=1000)
    finally:
        mt._stock, market_flags.collect = orig_stock, orig_flags

    eq(sorted(i["code"] for i in raw["items"]), ["000001", "100001"], "남은 종목")
    eq(raw["cut"]["관리종목"], 1, "관리종목 1")
    eq(raw["cut"]["투자주의환기종목"], 1, "환기종목 1")
    eq(raw["cut"]["1000원 미만"], 1, "동전주 1")
    eq(raw["seen"], 5, "조회한 전체")
    eq(raw["items"][0]["sector"], "화학", "업종은 KRX 분류를 그대로")
    print("test_collect_excludes_and_counts_why: OK")


def test_flags_return_empty_on_failure_rather_than_guessing():
    """
    관리종목 목록을 못 받으면 빈 집합. 그러면 아무것도 제외되지 않는다.

    멀쩡한 종목을 잘못 빼는 것보다, 빼야 할 것이 남는 편이 낫다 — 남은 것은
    화면에서 보이지만 잘못 빠진 것은 아무도 눈치채지 못한다.
    """
    orig = market_flags.fetch
    market_flags.fetch = lambda url: (_ for _ in ()).throw(RuntimeError("접속 실패"))
    try:
        eq(market_flags.admin_issues(), set(), "실패하면 빈 집합")
        eq(market_flags.alert_issues(), set(), "실패하면 빈 집합")
    finally:
        market_flags.fetch = orig
    print("test_flags_return_empty_on_failure_rather_than_guessing: OK")


def test_flags_parse_real_shapes():
    """네이버는 종목 링크로, KIND 는 표 칸의 6자리 숫자로 찾는다."""
    orig = market_flags.fetch
    market_flags.fetch = lambda url: (
        '<table><tr><td><a href="/item/main.naver?code=005930">삼성전자</a></td>'
        '<td>1,000</td></tr>'
        '<tr><td><a href="/item/main.naver?code=000660">SK하이닉스</a></td></tr>'
        '<td><a href="/sise/sise_index.naver">코스피</a></td></table>')
    try:
        eq(market_flags.admin_issues(), {"005930", "000660"}, "종목 링크만")
    finally:
        market_flags.fetch = orig

    market_flags.fetch = lambda url: (
        '<table><tr><th>종목코드</th><th>회사명</th></tr>'
        '<tr><td>123456</td><td>어떤회사</td><td>2026/01/02</td></tr>'
        '<tr><td>654321</td><td>다른회사</td></tr></table>'
        '<div>전화 021234567</div>')
    try:
        eq(market_flags.alert_issues(), {"123456", "654321"},
           "표 칸에 홀로 든 6자리만 — 본문의 긴 숫자는 아니다")
    finally:
        market_flags.fetch = orig
    print("test_flags_parse_real_shapes: OK")


# =============================================================================
# 뉴스
# =============================================================================

def test_news_parses_by_link_shape_not_class_name():
    doc = """
    <ul class="whatever-naver-calls-it-today">
      <li><a href="/news/news_read.naver?article_id=0005123456&office_id=277"><img src="t.jpg"></a>
          <a href="/news/news_read.naver?article_id=0005123456&office_id=277&date=20260731">
             코스피 사흘 만에 반등&hellip; 외국인 순매수 전환</a></li>
      <li><a href="https://n.news.naver.com/mnews/article/018/0006000111">
             SK하이닉스, HBM 증설에 12조 투입</a></li>
      <li><a href="/news/mainnews.naver?date=20260731">더보기</a></li>
      <li><a href="/item/main.naver?code=000660">SK하이닉스</a></li>
    </ul>"""
    got = mn.parse(doc)
    eq(len(got), 2, "기사 2건 (썸네일·더보기·종목링크 제외)")
    eq(got[0]["title"], "코스피 사흘 만에 반등… 외국인 순매수 전환", "HTML 엔티티 해제")
    eq(got[0]["url"],
       "https://finance.naver.com/news/news_read.naver?"
       "article_id=0005123456&office_id=277&date=20260731", "절대주소")
    eq(got[1]["url"], "https://n.news.naver.com/mnews/article/018/0006000111",
       "새 주소 형식도 잡는다")
    print("test_news_parses_by_link_shape_not_class_name: OK")


def test_news_returns_nothing_rather_than_garbage():
    """
    구조가 바뀌어 못 찾으면 빈 목록이다.

    아무 링크나 주워 담아 뉴스라고 내보내는 것보다 칸이 비는 편이 낫다 —
    빈 칸은 고장으로 보이지만, 엉뚱한 제목은 진짜 뉴스로 읽힌다.
    """
    eq(mn.parse("<html><body><a href='/item/main.naver?code=005930'>삼성전자</a>"
                "<a href='/sise/sise_index.naver'>코스피 지수 어쩌고저쩌고</a>"
                "</body></html>"), [], "기사 링크가 없으면 빈 목록")
    eq(mn.parse(""), [], "빈 문서")
    print("test_news_returns_nothing_rather_than_garbage: OK")


def test_news_dedupes_the_same_article_across_sections():
    """같은 기사가 mode 만 달라 두 번 걸리면 한 번만 남는다."""
    doc = ("<a href='/news/news_read.naver?article_id=1&office_id=2&mode=mainnews'>"
           "같은 기사 제목입니다</a>"
           "<a href='/news/news_read.naver?article_id=1&office_id=2&mode=RANK'>"
           "같은 기사 제목입니다</a>")
    eq(len(mn.parse(doc)), 1, "mode 만 다른 중복 제거")
    print("test_news_dedupes_the_same_article_across_sections: OK")


def test_news_decoding_ignores_a_lying_charset():
    """
    페이지가 UTF-8 이라고 써 놓고 EUC-KR 을 보내는 경우.

    실제로 이것 때문에 첫 수집의 제목이 전부 U+FFFD 로 깨져서 그대로 배포됐다.
    errors='replace' 로 뭉개지 말고, 엄격하게 풀어 틀린 코덱은 넘어가야 한다.
    """
    title = "코스피 사흘 만에 반등, 외국인 순매수 전환"
    raw = ('<meta charset="utf-8"><a href="/news/news_read.naver?article_id=1'
           '&office_id=2">' + title + "</a>").encode("cp949")
    got = mn.decode(raw, declared="utf-8")
    assert got and "�" not in got, f"깨진 글자가 남았다: {got!r}"
    eq(mn.parse(got)[0]["title"], title, "선언을 무시하고 실제 바이트로 풀었나")

    # 반대 경우 — euc-kr 이라 써 놓고 UTF-8 을 보내도 한글이 나와야 한다.
    raw2 = ('<meta charset="euc-kr"><a href="/news/news_read.naver?article_id=1'
            '&office_id=2">' + title + "</a>").encode("utf-8")
    got2 = mn.decode(raw2, declared="euc-kr")
    eq(mn.parse(got2)[0]["title"], title, "반대 방향도")
    print("test_news_decoding_ignores_a_lying_charset: OK")


def test_news_prefers_the_title_attribute():
    """목록에 보이는 글자는 네이버가 잘라 놨다. title 속성에 원문이 있다."""
    doc = ('<a href="/news/news_read.naver?article_id=1&office_id=2" '
           'title="반도체 슈퍼사이클 재점화, 메모리 3사 증설 경쟁">'
           '반도체 슈퍼사이클 재점화, 메모...</a>')
    eq(mn.parse(doc)[0]["title"], "반도체 슈퍼사이클 재점화, 메모리 3사 증설 경쟁",
       "잘린 글자 대신 title 속성")
    print("test_news_prefers_the_title_attribute: OK")


def _article(n, title=None):
    return (f'<a href="/news/news_read.naver?article_id={n}&office_id=2">'
            f'{title or f"기사 제목 {n} 입니다"}</a>')


def test_news_takes_only_the_named_block():
    """
    두 페이지가 **같은 사이드바**를 공유한다. 문서 순서대로 집으면 거기 실린
    보도자료가 주요뉴스 자리에 올라온다 — 실제로 '코인원 바우처 출시',
    '증권사 룰렛 이벤트'가 그렇게 배포됐다.

    실제 구조 (2026-08-03 확인):
        mainnews.naver        newsList 40건 · sub_tit_ticker 10 · right_list_1_2 8
        news_list.naver?RANK  simpleNewsList 25 · sub_tit_ticker 10 · right_list_1_2 8
    """
    main_doc = ('<div class="newsList">' + _article(1, "삼성전자 이사회 압박 본격화") +
                _article(2, "코스피 6300선 위태 개인 순매수") + '</div>'
                '<div class="sub_tit_ticker">' +
                _article(9, "코인원 바우처 서비스 출시") + '</div>'
                '<ul class="right_list_1_2">' +
                _article(8, "증권사 룰렛 이벤트 안내") + '</ul>')
    got = mn.parse(main_doc, block="newsList")
    eq([g["id"] for g in got], ["2/1", "2/2"], "가운데 목록만")
    assert not any("코인원" in g["title"] or "룰렛" in g["title"] for g in got), got

    eq(mn.parse(main_doc, block="newsList_v2"), [],
       "덩어리를 못 찾으면 빈 목록 — 엉뚱한 목록을 내보내지 않는다")
    print("test_news_takes_only_the_named_block: OK")


def test_news_keeps_an_article_that_appears_in_both_feeds():
    """
    많이 본 기사가 주요뉴스이기도 한 것은 자연스럽다. 예전에는 사이드바 때문에
    겹쳤던 것을 갈래끼리 빼서 가렸는데, 이제 덩어리가 갈리니 그럴 이유가 없다.
    """
    docs = {"a": '<div class="newsList">' + _article(1, "같은 기사 제목입니다") + '</div>',
            "b": '<div class="simpleNewsList">' + _article(1, "같은 기사 제목입니다") + '</div>'}
    of, ofeed = mn.fetch, mn.FEEDS
    mn.fetch = lambda u: (docs[u], "테스트")
    mn.FEEDS = [("주요뉴스", "a", "newsList"),
                ("많이 본 뉴스", "b", "simpleNewsList")]
    try:
        out = mn.collect()
    finally:
        mn.fetch, mn.FEEDS = of, ofeed
    eq([[i["id"] for i in g["items"]] for g in out["groups"]],
       [["2/1"], ["2/1"]], "겹쳐도 둘 다 남는다")
    eq(out["failed"], [], "겹침은 실패가 아니다")
    print("test_news_keeps_an_article_that_appears_in_both_feeds: OK")


def test_news_title_keeps_apostrophes():
    """
    제목 안의 작은따옴표에서 잘리면 안 된다.

    ["\\']([^"\\']+)["\\'] 로 두면 큰따옴표로 연 속성이라도 안쪽 작은따옴표에서
    멈춘다. 실제로 "해외부동산 1호 리츠인데" 처럼 문장 중간이 통째로 사라진
    제목이 배포됐다.
    """
    full = "해외부동산 1호 리츠인데 '파산 위기' 왜"
    doc = ('<a href="/news/news_read.naver?article_id=1&office_id=2" '
           f'title="{full}">해외부동산 1호 리츠인데...</a>')
    eq(mn.parse(doc)[0]["title"], full, "작은따옴표 너머까지 읽는다")
    print("test_news_title_keeps_apostrophes: OK")


def test_tree_backs_off_to_the_last_trading_day():
    """
    휴장일에 돌리면 하루씩 물러선다.

    지수 수집기는 기간을 통째로 요청해서 알아서 마지막 거래일로 떨어지지만
    시장 지도는 하루만 묻는다. 일요일 실행에서 이것 때문에 통째로 실패했다.
    """
    import datetime as dt

    class Frame:
        """휴장일에도 KRX 는 행을 돌려준다. 다만 종가가 전부 0 이다."""
        def __init__(self, closes):
            self.closes = closes
            self.empty = not closes

        def __contains__(self, k):
            return k == "종가"

        def __getitem__(self, k):
            class Col:
                def __init__(self, v): self.v = v
                def __gt__(self, n):
                    return Col([x > n for x in self.v])
                def any(self): return any(self.v)
            return Col(self.closes)

    class Calendar:
        """20260731(금)까지만 실제 거래가 있다."""
        def __init__(self):
            self.asked = []

        def get_market_sector_classifications(self, date, market="KOSPI"):
            self.asked.append(date)
            # 휴장일에도 943행이 오는데 값이 0 이다 — 이게 실제로 있었던 일이다.
            return Frame([0, 0, 0] if date > "20260731" else [70000, 5000, 320])

    cal = Calendar()
    got = mt.last_trading_day(cal, dt.date(2026, 8, 2))   # 일요일
    eq(got, "20260731", "금요일로 물러선다")
    eq(cal.asked, ["20260802", "20260801", "20260731"], "하루씩 거슬러 올라간다")
    assert not mt.traded(Frame([0, 0, 0])), "값이 0 인 표는 거래일이 아니다"
    assert mt.traded(Frame([1, 0, 0])), "하나라도 거래됐으면 거래일"

    class Dead:
        def get_market_sector_classifications(self, date, market="KOSPI"):
            raise RuntimeError("KRX 접속 실패")

    eq(mt.last_trading_day(Dead(), dt.date(2026, 8, 2), back=3), None,
       "끝까지 못 찾으면 None — 엉뚱한 날짜를 지어내지 않는다")
    print("test_tree_backs_off_to_the_last_trading_day: OK")


# =============================================================================
# 캘린더
# =============================================================================

def test_report_deadlines_follow_the_law():
    """
    분기·반기보고서는 기간 종료 후 45일, 사업보고서는 90일 (자본시장법 제160조).
    받아오는 것이 아니라 계산이므로 손으로 검산할 수 있다.
    """
    import datetime as dt
    rows = {r["period"]: r for r in mc.periods(dt.date(2026, 8, 3))}
    eq(rows["2026년 반기"]["deadline"], "20260814", "6/30 + 45일")
    eq(rows["2026년 반기"]["dday"], 11, "8/3 기준 11일 남음")
    eq(rows["2026년 3분기"]["deadline"], "20261114", "9/30 + 45일")
    eq(rows["2025년 사업보고서"]["deadline"], "20260331", "12/31 + 90일")
    eq(rows["2026년 1분기"]["dday"], -80, "이미 지난 마감은 음수")
    print("test_report_deadlines_follow_the_law: OK")


def test_quarter_label_matches_the_store_keys():
    """마감일 표를 store/facts 의 분기 키와 이어붙이려면 라벨이 같아야 한다."""
    eq(mc.quarter_label("20260331"), "2026Q1", "1분기")
    eq(mc.quarter_label("20260630"), "2026Q2", "반기 = 2분기")
    eq(mc.quarter_label("20260930"), "2026Q3", "3분기")
    eq(mc.quarter_label("20251231"), "2025Q4", "사업보고서 = 4분기")
    print("test_quarter_label_matches_the_store_keys: OK")


def test_calendar_leaves_meeting_dates_empty_when_it_cannot_fetch():
    """
    FOMC·금통위 날짜는 **지어낼 수 없는 값**이다. 못 받으면 빈 목록이고,
    무엇을 못 받았는지 결과에 적힌다. 지어내면 없는 회의를 기다리게 된다.
    """
    import datetime as dt
    orig = mc.fetch
    mc.fetch = lambda url, tries=2, ua=None, timeout=None: (_ for _ in ()).throw(
        RuntimeError("접속 실패"))
    try:
        out = mc.collect(dt.date(2026, 8, 3))
    finally:
        mc.fetch = orig
    eq(out["일정"], [], "받지 못하면 빈 목록")
    eq(sorted(out["failed"]), ["FOMC", "금통위", "미국 지표"],
       "무엇이 비었는지 남긴다")
    assert out["실적"], "실적 마감은 계산이라 항상 나온다"
    print("test_calendar_leaves_meeting_dates_empty_when_it_cannot_fetch: OK")


def test_fomc_takes_the_second_day_of_a_two_day_meeting():
    """
    FOMC 는 이틀 회의고 금리 결정은 둘째 날 나온다. '27-28' 이면 28일이다.
    """
    import datetime as dt
    orig = mc.fetch
    mc.fetch = lambda url, tries=2, ua=None, timeout=None: (
        '<h4>2026 FOMC Meetings</h4>'
        '<div class="panel">January 27-28</div>'
        '<div class="panel">March 17-18</div>')
    try:
        got = mc.fomc_dates(dt.date(2026, 1, 1))
    finally:
        mc.fetch = orig
    eq([g["date"] for g in got], ["20260128", "20260318"], "둘째 날")
    print("test_fomc_takes_the_second_day_of_a_two_day_meeting: OK")


def test_bok_ignores_a_stray_date_and_reads_the_table():
    """
    실제로 있었던 일: 페이지 구석의 '최종수정일 2025.11.26' 하나만 잡고 정작
    회의 표는 못 읽었는데, 값이 하나라도 있어서 성공으로 처리됐다.
    화면에는 250일 지난 '금통위'가 다가올 일정처럼 붙었다.

    표 칸 안만 보고, 연도 없는 'M월 D일' 도 함께 모으고, 최근·가까운 앞날에
    있는 것만 남긴다.
    """
    import datetime as dt
    today = dt.date(2026, 8, 3)
    orig = mc.fetch
    try:
        mc.fetch = lambda url, tries=2, ua=None, timeout=None: (
            '<div>최종수정일 2025.11.26</div><h3>2026년 통화정책방향</h3>'
            '<table><tr><td>1월 15일</td></tr><tr><td>8월 27일</td></tr></table>')
        eq([g["date"] for g in mc.bok_dates(today)], ["20260827"],
           "표를 읽고, 200일 지난 1월 회의는 뺀다")

        mc.fetch = lambda url, tries=2, ua=None, timeout=None: '<div>최종수정일 2025.11.26</div><table><tr><td>-</td></tr></table>'
        eq(mc.bok_dates(today), [], "표를 못 읽으면 빈 목록 — 옛 날짜를 내보내지 않는다")
    finally:
        mc.fetch = orig
    print("test_bok_ignores_a_stray_date_and_reads_the_table: OK")


FRED_RELEASES = {"releases": [
    {"id": 10, "name": "Consumer Price Index"},
    {"id": 46, "name": "Producer Price Index"},
    {"id": 50, "name": "Employment Situation"},
    {"id": 192, "name": "Job Openings and Labor Turnover Survey"},
    {"id": 999, "name": "Regional Employment and Unemployment"},
]}
FRED_DATES = {"release_dates": [
    {"release_id": 50, "date": "2026-09-04"},
    {"release_id": 10, "date": "2026-08-11"},
    {"release_id": 10, "date": "2026-08-11"},      # 같은 발표가 두 번
    {"release_id": 999, "date": "2026-08-05"},     # 우리가 안 고른 것
    {"release_id": 46, "date": "2026-08-13"},
    {"release_id": 192, "date": "bad-date"},       # 깨진 날짜
]}


def test_us_indicator_dates_keep_only_the_ones_people_watch():
    """
    FRED 는 한 해 수백 건을 낸다. 대부분 지역·업종 세부 통계라 그대로 넣으면
    달력이 그것으로 덮인다. 자주 회자되는 넷만 남긴다.

    (노동통계국은 403, FRED 발표일정 **화면**은 60초에도 ReadTimeout 이었다.
    같은 FRED 라도 API 는 작은 JSON 이라 빠르다.)
    """
    calls = []

    def fake(path, key, **p):
        calls.append(path)
        return FRED_RELEASES if path == "releases" else FRED_DATES

    orig, had = mc._fred, os.environ.get("FRED_API_KEY")
    mc._fred = fake
    os.environ["FRED_API_KEY"] = "x" * 32
    try:
        got = mc.bls_dates(dt.date(2026, 8, 3))
    finally:
        mc._fred = orig
        if had is None:
            os.environ.pop("FRED_API_KEY", None)
        else:
            os.environ["FRED_API_KEY"] = had
    eq([e["name"] for e in got],
       ["미국 소비자물가(CPI)", "미국 생산자물가(PPI)", "미국 고용보고서"],
       "고른 것만, 날짜순으로")
    eq(got[0]["date"], "20260811", "날짜")
    eq(got[0]["dday"], 8, "남은 날")
    eq(calls, ["releases", "releases/dates"], "두 번만 부른다")
    assert not any("Regional" in e["name"] for e in got), "세부 통계는 안 넣는다"
    print("test_us_indicator_dates_keep_only_the_ones_people_watch: OK")


def test_us_indicators_say_when_the_key_is_missing():
    """
    키가 없으면 그 칸만 비운다. 왜 비었는지는 파일에 남는다 —
    '못 받았다' 와 '키가 없다' 는 할 일이 다르다.
    """
    had = os.environ.pop("FRED_API_KEY", None)
    why = {}
    try:
        eq(mc.bls_dates(dt.date(2026, 8, 3), why), [], "키가 없으면 빈 목록")
        assert "비어 있음" in why["FRED"], why["FRED"]
    finally:
        if had is not None:
            os.environ["FRED_API_KEY"] = had
    print("test_us_indicators_say_when_the_key_is_missing: OK")


def test_diagnostics_never_carry_the_key():
    """
    예외 메시지에는 주소가 통째로 들어온다. ECOS 는 키를 **주소 경로**에,
    FRED 는 질의 문자열에 넣기 때문에, 예외를 그대로 진단에 적으면 키가
    공개 저장소에 커밋된다 — 실제로 ECOS 키가 그렇게 새어 나갔다.

    키를 환경변수에서 못 읽는 상황에서도 가려져야 한다.
    """
    ecos = "SBWPRN0VBX88RXDQVR1Y"
    msg = ("ConnectTimeout: HTTPSConnectionPool(host='ecos.bok.or.kr'): "
           f"Max retries exceeded with url: /api/StatisticSearch/{ecos}/json/kr")
    had = os.environ.pop("ECOS_KEY", None)
    try:
        assert ecos not in mm.safe(msg), mm.safe(msg)
        os.environ["ECOS_KEY"] = ecos
        assert ecos not in mm.safe(msg), mm.safe(msg)
    finally:
        os.environ.pop("ECOS_KEY", None)
        if had is not None:
            os.environ["ECOS_KEY"] = had

    fred = "abcdef0123456789abcdef0123456789"
    hit = f"400 Client Error for url: https://api.stlouisfed.org/fred/x?api_key={fred}"
    had2 = os.environ.pop("FRED_API_KEY", None)
    try:
        assert fred not in mc.safe(hit), mc.safe(hit)
    finally:
        if had2 is not None:
            os.environ["FRED_API_KEY"] = had2
    print("test_diagnostics_never_carry_the_key: OK")


def test_calendar_fetch_tries_again_before_giving_up():
    """
    금통위 페이지를 수집 단계에서는 못 받고 2분 뒤 진단 단계에서는 멀쩡히
    받은 적이 있다. 코드가 아니라 그때 한 번 안 온 것이었다. 한 번의 실패로
    그날 일정을 통째로 비우지 않는다.
    """
    calls = []

    def flaky(url, ua=None, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("일시 실패")
        return "<td>08월 27일(목)</td>"

    orig = mc._fetch_once
    mc._fetch_once = flaky
    try:
        got = mc.fetch("http://x")
    finally:
        mc._fetch_once = orig
    eq(len(calls), 2, "한 번 더 두드린다")
    eq(got, "<td>08월 27일(목)</td>", "두 번째에 받은 것을 돌려준다")
    print("test_calendar_fetch_tries_again_before_giving_up: OK")


def test_alert_issues_tries_the_next_url_when_one_404s():
    """
    처음 쓴 KIND 주소는 404 였다. 주소 규칙을 모르는 채 하나만 찍는 대신
    후보를 차례로 시도하고, 전부 실패하면 빈 집합을 돌려준다.
    """
    calls = []

    def fake(url):
        calls.append(url)
        if "searchAlertIssueMain" in url:
            raise RuntimeError("404")
        return "<table><tr><td>123456</td><td>어떤회사</td></tr></table>"

    orig = market_flags.fetch
    market_flags.fetch = fake
    try:
        eq(market_flags.alert_issues(), {"123456"}, "두 번째 후보에서 성공")
        eq(len(calls), 2, "첫 후보가 실패하면 다음으로 넘어간다")
        market_flags.fetch = lambda url: (_ for _ in ()).throw(RuntimeError("404"))
        eq(market_flags.alert_issues(), set(), "전부 실패하면 빈 집합")
    finally:
        market_flags.fetch = orig
    print("test_alert_issues_tries_the_next_url_when_one_404s: OK")


# =============================================================================
# 거시 지표
# =============================================================================

def test_rate_changes_are_the_events():
    """
    정책금리는 값이 바뀐 날이 곧 인상·인하일이다. 회의 일정표를 따로 긁어
    맞추는 것보다 값에서 뽑는 편이 틀릴 여지가 없다.
    """
    pts = {"20240101": 3.5, "20240201": 3.5, "20240301": 3.25,
           "20240401": 3.25, "20240501": 3.0}
    ev = mm.changes(pts)
    eq([(e["date"], e["dir"]) for e in ev],
       [("20240301", "인하"), ("20240501", "인하")], "바뀐 날만")
    eq(mm.changes({"a": 3.5, "b": 3.5000001}), [],
       "소수점 오차는 변화가 아니다")
    eq(mm.step_points(pts, ev),
       [["20240101", 3.5], ["20240301", 3.25], ["20240501", 3.0]],
       "계단은 시작·변화·끝만 있으면 된다")
    print("test_rate_changes_are_the_events: OK")


def test_after_returns_says_nothing_when_the_sample_is_tiny():
    """
    표본이 3회도 안 되면 통계를 내지 않는다.

    '두 번 중 두 번 올랐다'는 아무것도 알려주지 않으면서 확신만 준다.
    CLAUDE.md 의 문구 원칙이 표본을 요구하는 이유가 이것이다.
    """
    days = [f"2024{m:02d}{d:02d}" for m in range(1, 13) for d in (1, 15)]
    kospi = {d: 100.0 + i for i, d in enumerate(days)}     # 계속 오르는 지수
    two = [{"date": days[0], "dir": "인하", "from": 1, "to": 0},
           {"date": days[1], "dir": "인하", "from": 1, "to": 0}]
    eq(mm.after_returns(two, kospi, spans=(2,)), {}, "표본 2회면 말하지 않는다")

    three = two + [{"date": days[2], "dir": "인하", "from": 1, "to": 0}]
    got = mm.after_returns(three, kospi, spans=(2,))["인하"]["spans"]["2"]
    eq((got["표본"], got["상승"]), (3, 3), "표본 3회부터 센다")
    assert got["중앙값"] > 0, "오르는 지수인데 중앙값이 음수다"
    print("test_after_returns_says_nothing_when_the_sample_is_tiny: OK")


def test_after_returns_ignores_events_older_than_the_index():
    """
    지수가 있는 구간 밖의 사건은 재지 않는다.

    'day 이후 첫 거래일' 로만 잡아 뒀더니, 창(3년)보다 오래된 사건이 전부
    창의 첫날 하나로 몰렸다. 2016년 인상도 2018년 인상도 같은 날에서 재게
    되어 -4.97% 가 인상·인하·미국·한국 네 칸에 똑같이 찍혔다. 표본 19회라고
    적히지만 실제로는 같은 값 19개다.
    """
    days = [f"2024{m:02d}{d:02d}" for m in range(1, 13) for d in (1, 15)]
    kospi = {d: 100.0 + i for i, d in enumerate(days)}
    old = [{"date": f"20180{i}01", "dir": "인상", "from": 1, "to": 2}
           for i in (1, 2, 3)]
    now = [{"date": days[i], "dir": "인하", "from": 2, "to": 1} for i in (0, 1, 2)]
    out = mm.after_returns(old + now, kospi, spans=(2,))
    assert "인상" not in out, f"창 밖 사건은 재지 않는다: {out.get('인상')}"
    eq(out["인하"]["spans"]["2"]["표본"], 3, "창 안 사건은 그대로 잰다")
    print("test_after_returns_ignores_events_older_than_the_index: OK")


def test_after_returns_handles_a_holiday_decision_date():
    """
    결정일이 휴장일일 수 있다. 그 다음 거래일을 기준으로 잡는다.

    창 시작 **바로 앞**의 휴일까지만 봐 준다 (1/1 결정 · 1/2 개장은 같은
    사건이다). 몇 해 전 사건은 위 테스트대로 아예 재지 않는다.
    """
    kospi = {"20240102": 100.0, "20240103": 110.0, "20240104": 121.0}
    ev = [{"date": "20240101", "dir": "인하", "from": 1, "to": 0}]   # 휴장
    got = mm.after_returns(ev * 3, kospi, spans=(1,))["인하"]["spans"]["1"]
    eq(got["중앙값"], 10.0, "1/2 → 1/3 은 +10%")
    print("test_after_returns_handles_a_holiday_decision_date: OK")


def test_fallback_series_is_relabelled_not_disguised():
    """
    ECOS 키가 없으면 한국 기준금리를 FRED 의 단기금리로 대신한다. 그때
    **이름과 설명을 대용의 것으로 바꿔 단다.**

    콜금리는 기준금리를 따라다니지만 같은 값이 아니다. 같은 이름을 달면
    보는 사람이 대용을 원본으로 읽는다.
    """
    calls = []

    def fake_fred(fid, start):
        calls.append(fid)
        return {"20240101": 3.5, "20240301": 3.25} \
            if fid == "IR3TIB01KRM156N" else {}

    o_fred, o_ecos, o_kospi = mm.fetch_fred, mm.fetch_ecos, mm.fetch_kospi
    mm.fetch_fred, mm.fetch_kospi = fake_fred, (lambda *a, **k: {})
    try:
        mm.fetch_ecos = lambda spec, s, e, why=None: {}          # ECOS 안 됨
        kr = [x for x in mm.collect(10)["series"] if x["key"] == "kr_rate"][0]
        eq(kr["name"], "한국 단기금리", "대용이면 이름이 바뀐다")
        assert "대용" in kr["note"] and "IR3TIB01KRM156N" in kr["note"], kr["note"]
        assert kr["points"], "두 번째 후보에서 받아왔어야 한다"

        calls.clear()
        mm.fetch_ecos = lambda spec, s, e, why=None: {"20240101": 3.5, "20240301": 3.25}
        kr2 = [x for x in mm.collect(10)["series"] if x["key"] == "kr_rate"][0]
        eq(kr2["name"], "한국 기준금리", "원본이 되면 이름 그대로")
        assert "대용" not in kr2["note"]
        assert not [c for c in calls if "KR" in c], "원본이 되면 대용을 안 부른다"
    finally:
        mm.fetch_fred, mm.fetch_ecos, mm.fetch_kospi = o_fred, o_ecos, o_kospi
    print("test_fallback_series_is_relabelled_not_disguised: OK")


def test_fallback_is_not_treated_as_a_step_function():
    """
    대용(콜금리)은 계단이 아니다. 시장금리라 매일 조금씩 움직인다.

    기준금리처럼 '값이 바뀐 날 = 결정일'로 보면 2.541 → 2.527 같은 미세 변동이
    전부 '인하'로 찍힌다. 실제로 그렇게 24건이 배포됐다.
    """
    o_fred, o_ecos, o_kospi = mm.fetch_fred, mm.fetch_ecos, mm.fetch_kospi
    mm.fetch_fred = lambda fid, start: (
        {"20260301": 2.541, "20260401": 2.527, "20260501": 2.537}
        if fid == "IRSTCI01KRM156N" else {})
    mm.fetch_ecos = lambda spec, a, b: {}
    mm.fetch_kospi = lambda *a, **k: {}
    try:
        kr = [x for x in mm.collect(10)["series"] if x["key"] == "kr_rate"][0]
    finally:
        mm.fetch_fred, mm.fetch_ecos, mm.fetch_kospi = o_fred, o_ecos, o_kospi
    eq(kr["events"], [], "대용은 사건을 잡지 않는다")
    assert kr["points"], "그래도 선은 그린다"
    print("test_fallback_is_not_treated_as_a_step_function: OK")


def test_kospi_comes_from_the_file_the_index_step_already_wrote():
    """
    KRX 에서 직접 받으려다 두 번 실패했다 (빈 표 → KeyError: '지수명' →
    이름 조회를 꺼도 또 빈 표). market_signal 이 같은 실행에서 이미 받아 둔
    3년치를 읽는 편이 확실하고 KRX 도 덜 두드린다.

    저장값은 [시가,고가,저가,종가]다. 종가만 담던 옛 파일도 읽어야 한다.
    """
    d = os.path.join(tempfile.mkdtemp(), "sig.json")
    with open(d, "w", encoding="utf-8") as f:
        json.dump({"series": {"코스피": {
            "20260102": [10, 12, 9, 11],
            "20260105": 20,                       # 옛 꼴
            "20260106": [0, 0, 0, 0],             # 휴장·오류
        }}}, f)
    orig = mm.SIGNAL_PATH
    mm.SIGNAL_PATH = d
    try:
        eq(mm.fetch_kospi(3), {"20260102": 11.0, "20260105": 20.0},
           "종가만, 0 은 뺀다")
        mm.SIGNAL_PATH = os.path.join(d, "없음")
        eq(mm.fetch_kospi(3), {}, "파일이 없으면 빈 표 (이후 기록만 빈다)")
    finally:
        mm.SIGNAL_PATH = orig
    print("test_kospi_comes_from_the_file_the_index_step_already_wrote: OK")


GOLD_PAGE = """<table><thead>
<tr><th>날짜</th><th>매매기준율</th><th>전일대비</th><th colspan="2">실물 거래</th>
<th colspan="2">계좌 거래</th><th>기준 국제 금 시세</th><th>기준 원달러 환율</th></tr>
<tr><th>사실 때</th><th>파실 때</th><th>입금 시</th><th>해지 시</th></tr></thead><tbody>
<tr><td>2026.08.03</td><td>185,997.00</td><td>1,234.00</td><td>190,000</td><td>182,000</td>
<td>186,500</td><td>185,500</td><td>3,412.50</td><td>1,460.76</td></tr>
</tbody></table>"""


def _gold(doc, why):
    class R:
        status_code = 200
        content = doc.encode("cp949")
        def raise_for_status(self): pass
    orig = mm.requests.get
    mm.requests.get = lambda *a, **k: R()
    try:
        return mm.fetch_gold(dt.date(2026, 1, 1), pages=1, why=why)
    finally:
        mm.requests.get = orig


# =============================================================================
# 증시 현황판 — 간밤의 증시 · 주요 지표
# =============================================================================

def test_quote_is_found_wherever_the_wrapper_puts_it():
    """
    응답을 감싸는 모양이 주소마다 다르다. 시세가 들어 있는 dict 를 찾아내야지,
    맨 위 열쇠 이름을 못박으면 주소 하나가 바뀔 때마다 칸이 빈다.
    """
    flat = {"closePrice": "21,344.50", "compareToPreviousClosePrice": "-102.30"}
    wrapped = {"result": {"index": [flat]}}
    for body in (flat, wrapped, [flat]):
        q = mb.quote_of(body)
        eq(q["last"], 21344.5, "종가")
        eq(q["diff"], -102.3, "전일 대비")
    print("test_quote_is_found_wherever_the_wrapper_puts_it: OK")


def test_quote_fills_in_the_missing_half():
    """포인트만 오면 %를, %만 오면 포인트를 만든다. 둘 다 화면에 적어야 한다."""
    only_rate = mb.quote_of({"tradePrice": 110, "fluctuationsRatio": 10})
    eq(only_rate["diff"], 10.0, "10% 올라 110 이면 오른 폭은 10")
    only_diff = mb.quote_of({"tradePrice": 110, "changeValue": 10})
    eq(only_diff["rate"], 10.0, "100 에서 110 이면 10%")
    print("test_quote_fills_in_the_missing_half: OK")


def test_quote_ignores_a_dict_without_a_price():
    """값이 없는 dict 를 시세로 오해하면 0 이 지수로 나간다."""
    eq(mb.quote_of({"message": "ok", "code": 200}), {}, "시세 아님")
    eq(mb.quote_of({"closePrice": "0"}), {}, "0 은 시세가 아니다")
    print("test_quote_ignores_a_dict_without_a_price: OK")


def test_night_row_stays_empty_when_nothing_answers():
    """
    받을 곳을 못 찾으면 **비운다.** 코스피200 정규장 종가를 야간선물이라고
    적으면 그건 다른 숫자다. 대신 무엇을 두드렸는지 진단에 남긴다.
    """
    class Dead:
        status_code = 404
        content = b""
        def json(self): raise ValueError("no json")
    orig = mb.requests.get
    mb.requests.get = lambda *a, **k: Dead()
    try:
        why = {}
        spec = {"key": "k200_night", "name": "코스피200 야간선물",
                "syms": ["A"], "note": ""}
        eq(mb.fetch_quote(spec, why), {}, "빈 시세")
        assert "코스피200 야간선물" in why, why
        assert "404" in why["코스피200 야간선물"], why
    finally:
        mb.requests.get = orig
    print("test_night_row_stays_empty_when_nothing_answers: OK")


def test_fx_reads_the_base_rate_column_by_name():
    """
    환율 표도 머리가 두 줄이다. 칸 자리를 짐작하면 '현찰 사실 때'를
    매매기준율이라고 내보내게 된다 — 금에서 실제로 저질렀던 실수다.
    """
    doc = """
    <table><tr><th>날짜</th><th>매매기준율</th><th>전일대비</th>
      <th colspan="2">현찰</th></tr>
      <tr><th>사실 때</th><th>파실 때</th></tr>
      <tr><td>2026.08.03</td><td>1,460.70</td><td>3.20</td>
        <td>1,486.24</td><td>1,435.16</td></tr>
    </table>"""
    class R:
        status_code = 200
        content = doc.encode("cp949")
        def raise_for_status(self): pass
    orig = mb.requests.get
    mb.requests.get = lambda *a, **k: R()
    try:
        why = {}
        got = mb.fetch_fx("FX_USDKRW", dt.date(2026, 1, 1), pages=1, why=why)
        eq(got, {"20260803": 1460.70}, "매매기준율")
    finally:
        mb.requests.get = orig
    print("test_fx_reads_the_base_rate_column_by_name: OK")


def test_fx_stays_empty_when_the_column_is_gone():
    """칸을 못 찾으면 값을 내보내지 않는다. 틀린 환율보다 빈 칸이 낫다."""
    doc = "<table><tr><th>날짜</th><th>알 수 없는 칸</th></tr>" \
          "<tr><td>2026.08.03</td><td>1,460.70</td></tr></table>"
    class R:
        status_code = 200
        content = doc.encode("cp949")
        def raise_for_status(self): pass
    orig = mb.requests.get
    mb.requests.get = lambda *a, **k: R()
    try:
        why = {}
        eq(mb.fetch_fx("FX_USDKRW", dt.date(2026, 1, 1), pages=1, why=why),
           {}, "빈 값")
        assert "FX_USDKRW" in why, why
    finally:
        mb.requests.get = orig


# =============================================================================
# 헤드라인 뉴스 — 주제 묶기
# =============================================================================
    print("test_fx_stays_empty_when_the_column_is_gone: OK")


def test_josa_is_stripped_but_short_words_are_left_alone():
    """
    '코스피가' 와 '코스피는' 은 같은 낱말이다. 그렇다고 세 글자를 깎으면
    '순매도' 가 '순매' 가 되어 '순매수' 와 갈라지고, 엉뚱한 제목끼리 '순매'
    로 묶인다.
    """
    eq(mh.norm("코스피가"), "코스피", "조사를 뗀다")
    eq(mh.norm("삼성전자는"), "삼성전자", "조사를 뗀다")
    eq(mh.norm("순매도"), "순매도", "세 글자는 그대로")
    eq(mh.norm("금리"), "금리", "두 글자는 그대로")
    print("test_josa_is_stripped_but_short_words_are_left_alone: OK")


def test_same_event_written_by_two_papers_lands_in_one_topic():
    a = mh.tokens("코스피가 3% 급락…외국인 순매도 확대")
    b = mh.tokens("외국인 순매도에 코스피 급락 마감")
    assert mh.same_topic(a, b), (sorted(a), sorted(b))
    print("test_same_event_written_by_two_papers_lands_in_one_topic: OK")


def test_unrelated_headlines_do_not_get_merged():
    a = mh.tokens("코스피가 3% 급락…외국인 순매도 확대")
    for other in ["삼성전자 신형 갤럭시 공개 행사 열려",
                  "한국은행 기준금리 동결 결정",
                  "서울 아파트 매매가 3주째 보합"]:
        assert not mh.same_topic(a, mh.tokens(other)), other
    print("test_unrelated_headlines_do_not_get_merged: OK")


def test_one_shared_word_is_not_a_topic():
    """
    '코스피' 하나 겹친다고 같은 사건이 아니다. 낱말 하나로 묶으면 증시 기사가
    전부 한 덩어리가 되고, 그 덩어리의 기사 수는 아무것도 뜻하지 않는다.
    """
    a = mh.tokens("코스피 외국인 순매도 확대")
    b = mh.tokens("코스피 상장사 배당 확대 요구 커져")
    assert not mh.same_topic(a, b), (sorted(a), sorted(b))
    print("test_one_shared_word_is_not_a_topic: OK")


def test_clusters_are_ranked_by_how_many_papers_wrote_it():
    pool = [{"key": f"1/{i}", "title": t, "url": ""} for i, t in enumerate([
        "한국은행 기준금리 동결",
        "한국은행 기준금리 동결 결정",
        "기준금리 동결한 한국은행",
        "삼성전자 갤럭시 신제품 공개",
        "삼성전자 갤럭시 공개 행사",
    ])]
    groups = mh.cluster(pool)
    eq(len(groups[0]["items"]), 3, "가장 많이 쓰인 주제가 1위")
    eq(len(groups[1]["items"]), 2, "그 다음")
    print("test_clusters_are_ranked_by_how_many_papers_wrote_it: OK")


def test_cluster_compares_against_the_seed_not_the_whole_group():
    """
    묶음 전체와 비교하면 낱말이 붙을수록 그물이 커져 나중엔 아무거나 걸린다.
    씨앗과만 비교하므로, 씨앗과 안 겹치는 제목은 들어오지 못한다.
    """
    pool = [{"key": f"1/{i}", "title": t, "url": ""} for i, t in enumerate([
        "한국은행 기준금리 동결",
        "한국은행 기준금리 동결 발표",
        "환율 급등에 수출기업 비상",
    ])]
    groups = mh.cluster(pool)
    eq(len(groups[0]["items"]), 2, "씨앗과 겹치는 둘만")
    eq(len(groups), 2, "나머지는 따로")
    print("test_cluster_compares_against_the_seed_not_the_whole_group: OK")


def test_longest_body_wins_inside_a_topic():
    """같은 사건이라도 속보 한 줄과 해설 기사는 읽고 남는 것이 다르다."""
    pool = [{"key": "1/1", "title": "한국은행 기준금리 동결", "url": "a"},
            {"key": "1/2", "title": "한국은행 기준금리 동결 결정", "url": "b"}]
    bodies = {"1/1": {"chars": 300, "office": "가", "at": ""},
              "1/2": {"chars": 2400, "office": "나", "at": ""}}
    orig = mh.read_article
    mh.read_article = lambda key, why=None: bodies[key]
    try:
        rank = mh.build(pool, top=1)
    finally:
        mh.read_article = orig
    eq(rank[0]["n"], 2, "두 곳이 썼다")
    eq(rank[0]["url"], "b", "본문이 긴 쪽")
    eq(rank[0]["chars"], 2400, "글자수")
    print("test_longest_body_wins_inside_a_topic: OK")


def test_rank_survives_when_no_body_can_be_read():
    """
    본문을 못 읽어도 순위는 제목만으로 셌으므로 여전히 사실이다.
    빈 목록으로 만들면 헤드라인 칸이 통째로 사라진다.
    """
    pool = [{"key": "1/1", "title": "한국은행 기준금리 동결", "url": "a"},
            {"key": "1/2", "title": "한국은행 기준금리 동결 결정", "url": "b"}]
    orig = mh.read_article
    mh.read_article = lambda key, why=None: {}
    try:
        rank = mh.build(pool, top=1)
    finally:
        mh.read_article = orig
    eq(rank[0]["n"], 2, "순위는 남는다")
    eq(rank[0]["chars"], 0, "글자수는 모른다고 적는다")
    print("test_rank_survives_when_no_body_can_be_read: OK")


def test_unchanged_rank_is_not_written_again():
    """
    5분마다 도는데 매번 파일을 새로 쓰면 시각 하나 때문에 하루 288번
    커밋·배포가 된다. 순위가 그대로면 아무것도 쓰지 않는다.
    """
    rank = [{"n": 4, "title": "가", "url": "u"}]
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "market_headline.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": "옛날", "rank": rank}, f, ensure_ascii=False)
        assert mh.unchanged(rank, path), "같으면 True"
        assert not mh.unchanged([{"n": 5, "title": "가", "url": "u"}], path), \
            "건수가 바뀌면 False"
        assert not mh.unchanged(rank, os.path.join(d, "없는파일.json")), \
            "처음이면 False"
    print("test_unchanged_rank_is_not_written_again: OK")


def test_a_javascript_fragment_is_not_a_headline():
    """
    첫 실행에서 1위가 이것이었다:  '" style="display: none;">   (125건)

    네이버 화면 속 자바스크립트가 문자열로 들고 있는 HTML 조각을 <a> 로 읽은
    것이다. 조각들끼리는 '같은 제목'이라 한 묶음이 되어 125건짜리 1위가 됐다.
    묶기 전에 제목이 제목인지부터 본다.
    """
    for junk in ['\'" style="display: none;">', '<div class="x">기사</div>',
                 'function(a){ return a; }', '{{ title }}', 'AAPL up 3%']:
        assert not mh.ok_title(junk), junk
    assert mh.ok_title('[속보] 한국은행 기준금리 2.75% 동결')
    print("test_a_javascript_fragment_is_not_a_headline: OK")


def test_article_list_is_named_not_guessed_by_size():
    """
    처음엔 '그 쪽에서 가장 큰 덩어리'를 본문 목록으로 봤다. 실제로는 반대였다.

        덩어리/경제   83건 rl_border · 46건 sa_text

    rl_border 는 네 쪽에 똑같이 실린 '많이 본 뉴스' 다. 그게 제일 커서 네 쪽
    다 같은 83건을 집었고, 표본이 쪼그라들면서 경제 섹션 순위에 고깃집 기사가
    올라왔다. 크기로는 못 가른다 — 이름으로 가른다.
    """
    doc = """
    <div class="rl_border">
      <a href="/mnews/article/009/1" title="많이 본 뉴스 고깃집 사장 분노 사건">x</a>
      <a href="/mnews/article/009/2" title="많이 본 뉴스 오세훈 재판 유죄 판단">x</a>
      <a href="/mnews/article/009/3" title="많이 본 뉴스 연예기획사 대표 구속됐다">x</a>
    </div>
    <div class="sa_text"><a href="/mnews/article/001/1"
       title="한국은행 기준금리 동결 결정했다">x</a></div>
    <div class="sa_text"><a href="/mnews/article/001/2"
       title="코스피 외국인 순매도 확대되었다">x</a></div>"""
    why = {}
    rows = mh.parse_list(doc, "https://news.naver.com/section/101",
                         "sa_text", why, "경제")
    eq([r["key"] for r in rows], ["001/1", "001/2"],
       "작아도 이름이 맞는 덩어리")
    assert "rl_border" in why["덩어리/경제"], why
    print("test_article_list_is_named_not_guessed_by_size: OK")


def test_feed_goes_blank_when_the_block_name_is_gone():
    """
    이름이 바뀌면 그 갈래는 빈다. 엉뚱한 목록을 헤드라인이라고 내보내는 것보다
    낫다 — 첫 실행에서 바로 그 일이 일어났다.
    """
    doc = ('<div class="rl_border"><a href="/mnews/article/009/1" '
           'title="많이 본 뉴스 고깃집 사장 분노 사건">x</a></div>')
    why = {}
    eq(mh.parse_list(doc, "https://x/", "sa_text", why, "경제"), [], "빈 목록")
    assert "못 찾아" in why["목록/경제"], why
    print("test_feed_goes_blank_when_the_block_name_is_gone: OK")


def test_office_drops_the_portal_that_only_carried_it():
    """og:article:author 는 '주간동아 | 네이버' 로 온다. 쓴 곳은 네이버가 아니다."""
    eq(mh.OFFICE_TAIL.sub("", "주간동아 | 네이버").strip(), "주간동아", "꼬리를 뗀다")
    eq(mh.OFFICE_TAIL.sub("", "연합뉴스 · 네이버 뉴스").strip(), "연합뉴스", "꼬리를 뗀다")
    eq(mh.OFFICE_TAIL.sub("", "한국경제").strip(), "한국경제", "꼬리가 없으면 그대로")
    print("test_office_drops_the_portal_that_only_carried_it: OK")



def test_gold_reads_the_international_column_not_the_first_number():
    """
    날짜 뒤 첫 숫자는 **매매기준율(1그램 원)** 이다. 그걸 집어서 '185,821.74
    달러' 를 내보낸 적이 있다. 국제 금은 온스당 3천 달러대다.

    표 머리가 두 줄이라(colspan) '기준 국제 금 시세' 가 몇 번째 칸인지는
    세어 봐야 안다. 자리를 짐작하지 않고 머리에서 편다.
    """
    eq(mm.flat_headers(GOLD_PAGE)[3], "실물 거래 사실 때", "colspan 을 펴서 붙인다")
    why = {}
    eq(_gold(GOLD_PAGE, why), {"20260803": 3412.5}, "국제 금 시세 칸을 읽는다")
    assert "7번째" in why["금/자리"], why["금/자리"]
    print("test_gold_reads_the_international_column_not_the_first_number: OK")


def test_gold_stays_empty_when_the_column_is_gone():
    """
    표가 바뀌어 그 칸을 못 찾으면 값을 내보내지 않는다.
    단위가 틀린 숫자보다 빈 칸이 낫다 — 한 번 그렇게 내보낸 적이 있다.
    """
    why = {}
    eq(_gold(GOLD_PAGE.replace("기준 국제 금 시세", "거래량"), why), {},
       "칸이 없으면 빈 표")
    assert "못 찾아" in why["금"], why["금"]
    assert why["금/칸"], "무엇이 왔는지는 남긴다"
    print("test_gold_stays_empty_when_the_column_is_gone: OK")


# =============================================================================
# 지수보다 강한 종목
# =============================================================================

class FakeRow(dict):
    def get(self, k, d=None): return dict.get(self, k, d)


class FakeDF:
    """pykrx 가 돌려주는 표 흉내. traded() 가 보는 것까지 맞춘다."""
    def __init__(self, rows): self.rows = rows; self.empty = not rows
    def __contains__(self, k): return k == "종가"
    def __len__(self): return len(self.rows)
    def __getitem__(self, k):
        class Col:
            def __init__(s, v): s.v = v
            def __gt__(s, n): return Col([x > n for x in s.v])
            def any(s): return any(s.v)
        return Col([r.get("종가", 0) for _, r in self.rows])
    def iterrows(self): return iter([(c, FakeRow(r)) for c, r in self.rows])


def _bar(close, vol=100, val=1000, chg=1.0):
    return {"시가": close, "고가": close, "저가": close, "종가": close,
            "거래량": vol, "거래대금": val, "등락률": chg}


def test_strong_drops_stocks_without_a_full_window():
    """
    20일을 다 못 채운 종목은 뺀다.

    중간에 거래정지된 날이 있으면 19개로 평균이 나는데, 그것을 20일선이라고
    부르면 이격도가 조용히 틀린다. 모자란 종목은 아예 계산하지 않는다.
    """
    hist = []
    for i in range(3):
        rows = [("000001", _bar(100 + i))]
        if i != 1:                                   # 000002 는 하루 빠진다
            rows.append(("000002", _bar(200 + i)))
        hist.append((f"2026080{i+1}", FakeDF(rows)))
    closes, today = mst.series_of(hist, days=3)
    eq(list(closes), ["000001"], "구멍 난 종목은 빠진다")
    eq(closes["000001"], [100.0, 101.0, 102.0], "오래된 것부터")
    eq(today["000001"]["close"], 102, "오늘 값은 마지막 날")
    print("test_strong_drops_stocks_without_a_full_window: OK")


def test_strong_skips_holidays_and_returns_oldest_first():
    """휴장일은 행이 와도 종가가 0 이다. 날짜 수로 세면 안 된다."""
    seen = []

    class Stock:
        def get_market_ohlcv_by_ticker(self, date, market):
            seen.append(date)
            if date in ("20260802", "20260801"):     # 주말
                return FakeDF([("000001", _bar(0))])
            return FakeDF([("000001", _bar(100))])

    hist = mst.fetch_history(Stock(), dt.date(2026, 8, 3), days=2)
    eq([d for d, _ in hist], ["20260731", "20260803"], "휴장일을 건너뛰고 오름차순")
    eq(seen, ["20260803", "20260802", "20260801", "20260731"], "하루씩 뒤로")
    print("test_strong_skips_holidays_and_returns_oldest_first: OK")


BIG = 200_000_000_000       # 문턱(1,000억)을 넉넉히 넘는 값


def _r(code, disp, val=BIG, market="코스피", chg=0.0, cap=BIG):
    return {"code": code, "name": code, "market": market, "disparity": disp,
            "value": val, "chg": chg, "cap": cap}


def test_strong_is_measured_against_that_market_index():
    """
    '강하다'는 지수 이격도보다 위라는 뜻이다. 등락률이 아니라 이격도로 잰다.

    정렬은 1차 이격도, 2차 거래대금. 이격이 같으면 거래가 실린 쪽이 앞이다.
    """
    rows = [_r("A", 5.0, BIG), _r("B", 5.0, BIG * 9), _r("C", -1.0),
            _r("D", 12.0, market="코스닥", chg=30.0),
            _r("E", -9.0, market="코스닥", chg=-30.0)]
    out = mst.build(rows, {"코스피": 0.0, "코스닥": 20.0}, top=10)

    k = out["markets"]["코스피"]
    eq([x["code"] for x in k["strong"]], ["B", "A"],
       "이격이 같으면 거래대금이 큰 쪽이 앞")
    eq(k["strong_total"], 2, "지수(0.00%)보다 아래인 C 는 빠진다")
    eq(out["markets"]["코스닥"]["strong"], [],
       "지수가 +20% 인 날은 +12% 도 강한 게 아니다")
    eq([x["code"] for x in out["급상승"]][0], "D", "급상승은 등락률순")
    eq([x["code"] for x in out["급하락"]][0], "E", "급하락도 등락률순")
    print("test_strong_is_measured_against_that_market_index: OK")


def test_strong_drops_stocks_you_cannot_actually_trade():
    """
    거래대금·시가총액 문턱을 못 넘으면 네 목록 어디에도 안 올린다.

    안 걸렀을 때 코스닥 1~6위가 거래대금 1억·4,438만·3,105만원짜리로
    채워졌다. 못 사고 못 파는 종목이다. 게다가 20일 사이 몇 배가 된 종목이라
    이격도가 +263% 로 나오는데, 그쯤 되면 '20일선 대비'라는 말의 뜻이 없다.

    문턱을 넘은 종목이 몇이었는지는 세어서 남긴다 — 한산한 날 목록이 왜
    짧은지 화면이 스스로 말해야 한다.
    """
    rows = [_r("크다", 3.0), _r("거래없음", 99.0, val=1_000_000),
            _r("잔챙이", 88.0, cap=1_000_000),
            _r("떨어짐", -50.0, chg=-29.0)]
    out = mst.build(rows, {"코스피": 0.0}, top=10)
    eq([x["code"] for x in out["markets"]["코스피"]["strong"]], ["크다"],
       "이격 +99% 라도 거래가 없으면 안 올린다")
    eq(out["markets"]["코스피"]["counted"], 4, "계산한 종목 수는 그대로")
    eq(out["markets"]["코스피"]["liquid"], 2, "문턱을 넘은 종목 수")
    eq([x["code"] for x in out["급하락"]], ["떨어짐", "크다"],
       "급상승·급하락에도 같은 문턱을 쓴다")
    # 분포는 거래대금만 본다 (시총은 따로 거른다). 셋이 1,000억을 넘는다.
    eq(out["spread"]["코스피"]["1000억 이상"], 3, "분포를 남겨 문턱을 다시 잰다")
    eq(out["spread"]["코스피"]["3000억 이상"], 0, "그 위 눈금도 함께 남긴다")
    print("test_strong_drops_stocks_you_cannot_actually_trade: OK")


def test_strong_leaves_the_list_empty_when_the_index_is_unknown():
    """
    지수 이격도를 못 읽으면 '강한 종목'을 만들지 않는다.

    기준 없이 이격도 상위만 뽑으면 그건 '지수보다 강한 종목'이 아니라 그냥
    많이 오른 종목이다. 이름과 다른 것을 보여주느니 비운다.
    """
    out = mst.build([_r("A", 9.0, chg=1.0)], {}, top=5)
    eq(out["markets"]["코스피"]["strong"], [], "기준이 없으면 비운다")
    eq(out["markets"]["코스피"]["index_disparity"], None, "기준이 없다고 적는다")
    eq(len(out["급상승"]), 1, "급상승은 지수와 무관하니 그대로 나온다")
    print("test_strong_leaves_the_list_empty_when_the_index_is_unknown: OK")


def test_strong_tags_themes_but_only_two():
    """
    테마에 엮인 종목은 이격이 단기간에 벌어진다. 감추지 않고 어느 테마인지
    적는다. 다만 한 종목이 대여섯 테마에 걸리는 일이 흔해 둘까지만 적는다.
    """
    d = os.path.join(tempfile.mkdtemp(), "t.json")
    with open(d, "w", encoding="utf-8") as f:
        json.dump({"groups": [
            {"name": "반도체", "subs": [
                {"name": "HBM·메모리", "codes": ["000660", "005930"]},
                {"name": "파운드리", "codes": ["000660"]}]},
            {"name": "로봇", "subs": [{"name": "협동로봇", "codes": ["000660"]}]},
        ]}, f)
    idx = mst.theme_index(d)
    eq(len(idx["000660"]), 2, "뱃지는 두 개까지")
    eq(idx["000660"][0]["sub"], "HBM·메모리", "먼저 나온 것부터")
    eq([t["sub"] for t in idx["005930"]], ["HBM·메모리"], "한 테마면 하나")
    eq(mst.theme_index(os.path.join(d, "없음")), {}, "파일이 없으면 빈 표")
    print("test_strong_tags_themes_but_only_two: OK")


def test_strong_reads_the_same_index_number_the_front_page_shows():
    """
    지수 이격도는 여기서 다시 계산하지 않고 market_signal 이 낸 값을 읽는다.
    따로 계산하면 같은 화면에 '-7.00%' 와 '-6.93%' 가 같이 나온다.
    """
    d = os.path.join(tempfile.mkdtemp(), "s.json")
    with open(d, "w", encoding="utf-8") as f:
        json.dump({"computed": {"indices": {
            "코스피": {"disparity": -7.0}, "코스닥": {"disparity": None}}}}, f)
    eq(mst.index_disparity(d), {"코스피": -7.0}, "값이 없는 지수는 안 넣는다")
    eq(mst.index_disparity(os.path.join(d, "없음")), {}, "파일이 없으면 빈 표")
    print("test_strong_reads_the_same_index_number_the_front_page_shows: OK")


# =============================================================================
# 테마
# =============================================================================

# 실제 구조에서 뽑아 줄인 것 (theme_probe.py, 2026-08-03).
# 열: 테마명 · 전일대비 · 전체 · 상승 · 보합 · 하락 · 등락그래프
THEME_LIST = """
<table summary="테마별 시세">
<tr><th>테마명</th><th>전일대비</th></tr>
<tr><td><a href="/sise/sise_group_detail.naver?type=theme&amp;no=505">로봇(협동로봇)</a></td>
<td><span class="tah p11 red01">+6.63%</span></td>
<td>71</td><td>66</td><td>0</td><td>5</td>
<td><div class="graph"><span style="width:99%"></span><span class="blind">99%</span></div></td></tr>
<tr><td><a href="/sise/sise_group_detail.naver?type=theme&amp;no=64">HBM(고대역폭메모리)</a></td>
<td><span class="tah p11 nv01">-1.20%</span></td>
<td>18</td><td>3</td><td>1</td><td>14</td>
<td><div class="graph"><span class="blind">17%</span></div></td></tr>
<tr><td><a href="/sise/sise_group_detail.naver?type=theme&amp;no=99">우주항공</a></td>
<td><span>+2.00%</span></td><td>9</td><td>7</td><td>0</td><td>2</td></tr>
<tr><td><a href="/sise/sise_group_detail.naver?type=theme&amp;no=999">알수없는테마</a></td>
<td><span>+0.10%</span></td><td>5</td><td>2</td><td>0</td><td>3</td></tr>
</table>
"""


def test_theme_row_reads_the_numbers_that_follow_the_link():
    """
    <tr> 로 자르지 않고 테마 링크를 기준으로 자른다. 표 구조가 바뀌어도
    링크 뒤에 등락률·종목수가 오는 순서는 잘 안 바뀐다.

    등락그래프 칸의 '99%' 를 종목 수로 착각하면 안 된다 — 소수점 없는 %는
    등락률이 아니고, % 앞의 숫자는 종목 수도 아니다.
    """
    rows = mth.parse_list(THEME_LIST)
    eq([r["no"] for r in rows], [505, 64, 99, 999], "테마 번호")
    eq(rows[0]["name"], "로봇(협동로봇)", "테마 이름")
    eq((rows[0]["chg"], rows[0]["n"], rows[0]["up"], rows[0]["flat"],
        rows[0]["down"]), (6.63, 71, 66, 0, 5), "등락률과 종목 수")
    eq(rows[1]["chg"], -1.20, "내린 날은 음수")
    print("test_theme_row_reads_the_numbers_that_follow_the_link: OK")


def test_theme_members_come_from_the_biggest_block_not_the_sidebar():
    """
    네이버 금융 페이지에는 '인기 검색 종목' 사이드바가 딸려 있고 거기에도
    같은 모양의 종목 링크가 있다. 뉴스에서 링크 모양만 보고 집었다가 사이드바를
    주요뉴스로 내보낸 적이 있다 — 여기서는 같은 실수를 하지 않는다.

    편입 종목표는 종목 링크가 가장 많이 모인 덩어리다. 어느 덩어리였는지도
    함께 돌려받아 store/ 에 남긴다.
    """
    doc = """
    <div class="aside_area"><ul class="lst"><li><a href="/item/main.naver?code=005930">삼성전자</a></li>
    <li><a href="/item/main.naver?code=000660">SK하이닉스</a></li></ul></div>
    <div class="box_type_l"><table class="type_5"><tbody>
    <tr><td><a href="/item/main.naver?code=108320">LX세미콘</a></td><td>1,000</td></tr>
    <tr><td><a href="/item/main.naver?code=108320"><img src="x.gif"></a></td><td>1,000</td></tr>
    <tr><td><a href="/item/main.nhn?code=042700&amp;page=1">한미반도체</a></td><td>2,000</td></tr>
    <tr><td><a href="/item/main.naver?code=095340">ISC</a></td><td>3,000</td></tr>
    </tbody></table></div>
    """
    got, marker = mth.parse_members(doc)
    eq([m["code"] for m in got], ["108320", "042700", "095340"],
       "사이드바 종목은 빼고, 같은 종목은 한 번만")
    eq(got[0]["name"], "LX세미콘", "글자가 있는 링크를 남긴다")
    eq(marker, "type_5", "어느 덩어리에서 집었는지 함께 돌려준다")
    eq(mth.parse_members("<p>종목이 없다</p>"), ([], ""), "없으면 빈 목록")
    print("test_theme_members_come_from_the_biggest_block_not_the_sidebar: OK")


THEMES = [
    {"name": "반도체", "subs": [
        {"name": "HBM·메모리", "match": ["HBM", "메모리"]},
        {"name": "소부장", "match": ["반도체장비"]}]},
    {"name": "로봇", "subs": [{"name": "협동로봇", "match": ["로봇"]}]},
]


def test_theme_match_prefers_the_longer_fragment():
    """긴 조각일수록 좁은 뜻이다. '메모리' 와 'HBM' 이 함께 걸리면 긴 쪽."""
    eq(mth.match_theme("HBM(고대역폭메모리)", THEMES), ("반도체", "HBM·메모리"),
       "긴 조각이 이긴다")
    eq(mth.match_theme("반도체장비", THEMES), ("반도체", "소부장"), "장비")
    eq(mth.match_theme("알수없는테마", THEMES), None, "안 걸리면 None")
    print("test_theme_match_prefers_the_longer_fragment: OK")


def test_theme_group_change_is_weighted_by_stock_count():
    """
    대분류 등락률은 그 아래 테마들의 평균인데, 테마마다 종목 수가 다르다.
    3종목짜리 테마와 70종목짜리 테마를 같은 무게로 두면 작은 테마가 화면을
    흔든다. 종목 수로 눌러 평균을 낸다.
    """
    rows = [{"no": 1, "name": "HBM", "chg": 10.0, "n": 3},
            {"no": 2, "name": "반도체장비", "chg": 0.0, "n": 27}]
    out = mth.build(rows, THEMES, {1: [{"code": "000660", "name": "가"}],
                                   2: [{"code": "000660", "name": "가"},
                                       {"code": "042700", "name": "나"}]})
    semi = [g for g in out["groups"] if g["name"] == "반도체"][0]
    eq(semi["chg"], 1.0, "(10*3 + 0*27) / 30")
    eq(semi["themes"], 2, "테마 수")
    eq(semi["stocks"], 2, "겹치는 종목은 한 번만 센다")
    robot = [g for g in out["groups"] if g["name"] == "로봇"][0]
    eq((robot["chg"], robot["themes"]), (None, 0),
       "걸린 테마가 없으면 등락률을 지어내지 않는다")
    print("test_theme_group_change_is_weighted_by_stock_count: OK")


def test_theme_unmatched_is_counted_not_hidden():
    """
    우리 분류에 안 걸린 테마는 조각을 고쳐야 한다는 신호다. 목록은 store/ 에
    남기고 화면에는 몇 개인지를 넘긴다. 조용히 지우면 영영 못 고친다.
    """
    rows = [{"no": 1, "name": "HBM", "chg": 1.0, "n": 3},
            {"no": 9, "name": "알수없는테마", "chg": 5.0, "n": 4},
            {"no": 8, "name": "또다른테마", "chg": 5.0, "n": 4}]
    out = mth.build(rows, THEMES, {})
    eq(out["unmatched"], ["또다른테마", "알수없는테마"], "미분류를 그대로 남긴다")
    slim = mth.slim_of({**out, "rows": rows, "markers": {"type_5": 2},
                        "detail_failed": []})
    eq(slim["unmatched"], 2, "화면에는 개수만")
    eq([k for k in ("rows", "markers", "detail_failed") if k in slim], [],
       "화면이 안 쓰는 것은 안 내보낸다")
    print("test_theme_unmatched_is_counted_not_hidden: OK")


if __name__ == "__main__":
    test_screen_all_conditions_must_hold()
    test_screen_drops_unknown_values()
    test_screen_excludes_foreign_currency_and_halted()
    test_screen_ranks_by_pbr_over_roe_and_states_the_rule()
    test_picks_first_run_says_it_has_nothing_to_compare()
    test_picks_diff_against_the_previous_collection_day()
    test_picks_rerun_on_the_same_day_compares_with_the_day_before()
    test_picks_history_survives_a_broken_file_and_stays_bounded()
    test_sector_change_is_cap_weighted()
    test_tiles_are_cut_per_market_but_aggregates_are_not()
    test_collect_excludes_and_counts_why()
    test_flags_return_empty_on_failure_rather_than_guessing()
    test_flags_parse_real_shapes()
    test_news_parses_by_link_shape_not_class_name()
    test_news_returns_nothing_rather_than_garbage()
    test_news_dedupes_the_same_article_across_sections()
    test_news_decoding_ignores_a_lying_charset()
    test_news_prefers_the_title_attribute()
    test_news_title_keeps_apostrophes()
    test_news_takes_only_the_named_block()
    test_news_keeps_an_article_that_appears_in_both_feeds()
    test_tree_backs_off_to_the_last_trading_day()
    test_report_deadlines_follow_the_law()
    test_quarter_label_matches_the_store_keys()
    test_calendar_leaves_meeting_dates_empty_when_it_cannot_fetch()
    test_fomc_takes_the_second_day_of_a_two_day_meeting()
    test_bok_ignores_a_stray_date_and_reads_the_table()
    test_alert_issues_tries_the_next_url_when_one_404s()
    test_us_indicator_dates_keep_only_the_ones_people_watch()
    test_us_indicators_say_when_the_key_is_missing()
    test_calendar_fetch_tries_again_before_giving_up()
    test_diagnostics_never_carry_the_key()
    test_rate_changes_are_the_events()
    test_after_returns_says_nothing_when_the_sample_is_tiny()
    test_after_returns_handles_a_holiday_decision_date()
    test_after_returns_ignores_events_older_than_the_index()
    test_fallback_series_is_relabelled_not_disguised()
    test_fallback_is_not_treated_as_a_step_function()
    test_kospi_comes_from_the_file_the_index_step_already_wrote()
    test_theme_row_reads_the_numbers_that_follow_the_link()
    test_theme_members_come_from_the_biggest_block_not_the_sidebar()
    test_theme_match_prefers_the_longer_fragment()
    test_theme_group_change_is_weighted_by_stock_count()
    test_theme_unmatched_is_counted_not_hidden()
    test_gold_reads_the_international_column_not_the_first_number()
    test_gold_stays_empty_when_the_column_is_gone()
    test_strong_drops_stocks_without_a_full_window()
    test_strong_skips_holidays_and_returns_oldest_first()
    test_strong_is_measured_against_that_market_index()
    test_strong_drops_stocks_you_cannot_actually_trade()
    test_strong_leaves_the_list_empty_when_the_index_is_unknown()
    test_strong_tags_themes_but_only_two()
    test_strong_reads_the_same_index_number_the_front_page_shows()
    test_quote_is_found_wherever_the_wrapper_puts_it()
    test_quote_fills_in_the_missing_half()
    test_quote_ignores_a_dict_without_a_price()
    test_night_row_stays_empty_when_nothing_answers()
    test_fx_reads_the_base_rate_column_by_name()
    test_fx_stays_empty_when_the_column_is_gone()
    test_josa_is_stripped_but_short_words_are_left_alone()
    test_same_event_written_by_two_papers_lands_in_one_topic()
    test_unrelated_headlines_do_not_get_merged()
    test_one_shared_word_is_not_a_topic()
    test_clusters_are_ranked_by_how_many_papers_wrote_it()
    test_cluster_compares_against_the_seed_not_the_whole_group()
    test_longest_body_wins_inside_a_topic()
    test_rank_survives_when_no_body_can_be_read()
    test_unchanged_rank_is_not_written_again()
    test_a_javascript_fragment_is_not_a_headline()
    test_article_list_is_named_not_guessed_by_size()
    test_feed_goes_blank_when_the_block_name_is_gone()
    test_office_drops_the_portal_that_only_carried_it()
    print("\nALL BOARD TESTS PASSED")
