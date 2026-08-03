# -*- coding: utf-8 -*-
"""
첫 화면을 채우는 세 가지 — 조건 필터 · 시장 지도 · 뉴스 — 테스트.

셋 다 바깥에서 받아오는 것이 있지만, 받아오는 부분과 계산·파싱을 갈라 두고
여기서는 계산·파싱만 손으로 검산한다. 네트워크가 없어도 돌아야 한다.

실행:  python tests/test_board.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from screener import screen
import market_tree as mt
import market_news as mn
import market_flags
import market_calendar as mc


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


def test_news_second_group_skips_what_the_first_took():
    """
    '많이 본 뉴스' 페이지에도 상단에 주요뉴스 블록이 통째로 들어 있다.
    앞에서부터 세면 두 갈래가 똑같은 목록이 되므로, 앞 갈래가 가져간 기사는
    건너뛰고 그 아래에 있는 것을 집는다.
    """
    first = _article(1) + _article(2)
    second = _article(1) + _article(2) + _article(7) + _article(8)
    docs = {"a": first, "b": second}
    orig_fetch, orig_feeds = mn.fetch, mn.FEEDS
    mn.fetch = lambda url: (docs[url], "테스트")
    mn.FEEDS = [("주요뉴스", "a"), ("많이 본 뉴스", "b")]
    try:
        out = mn.collect()
    finally:
        mn.fetch, mn.FEEDS = orig_fetch, orig_feeds
    eq([i["id"] for i in out["groups"][0]["items"]], ["2/1", "2/2"], "앞 갈래")
    eq([i["id"] for i in out["groups"][1]["items"]], ["2/7", "2/8"],
       "뒤 갈래는 겹치지 않는 것만")
    eq(out["failed"], [], "둘 다 채워졌다")
    print("test_news_second_group_skips_what_the_first_took: OK")


def test_news_blanks_a_group_that_is_purely_a_duplicate():
    """
    겹치는 것을 빼고 나서 아무것도 안 남으면 그 갈래는 빈다.

    같은 목록에 서로 다른 이름표를 붙이는 것은 빈 칸보다 나쁘다 — 빈 칸은
    고장으로 보이지만 같은 목록은 진짜 두 갈래로 읽힌다.
    """
    same = _article(1) + _article(2)
    orig_fetch, orig_feeds = mn.fetch, mn.FEEDS
    mn.fetch = lambda url: (same, "테스트")
    mn.FEEDS = [("주요뉴스", "a"), ("많이 본 뉴스", "b")]
    try:
        out = mn.collect()
    finally:
        mn.fetch, mn.FEEDS = orig_fetch, orig_feeds
    eq(len(out["groups"][0]["items"]), 2, "앞쪽은 남는다")
    eq(out["groups"][1]["items"], [], "뒤쪽은 빈다")
    eq(out["failed"], ["많이 본 뉴스"], "빈 갈래를 기록한다")
    print("test_news_blanks_a_group_that_is_purely_a_duplicate: OK")


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
    mc.fetch = lambda url: (_ for _ in ()).throw(RuntimeError("접속 실패"))
    try:
        out = mc.collect(dt.date(2026, 8, 3))
    finally:
        mc.fetch = orig
    eq(out["일정"], [], "받지 못하면 빈 목록")
    eq(sorted(out["failed"]), ["FOMC", "금통위"], "무엇이 비었는지 남긴다")
    assert out["실적"], "실적 마감은 계산이라 항상 나온다"
    print("test_calendar_leaves_meeting_dates_empty_when_it_cannot_fetch: OK")


def test_fomc_takes_the_second_day_of_a_two_day_meeting():
    """
    FOMC 는 이틀 회의고 금리 결정은 둘째 날 나온다. '27-28' 이면 28일이다.
    """
    import datetime as dt
    orig = mc.fetch
    mc.fetch = lambda url: (
        '<h4>2026 FOMC Meetings</h4>'
        '<div class="panel">January 27-28</div>'
        '<div class="panel">March 17-18</div>')
    try:
        got = mc.fomc_dates(dt.date(2026, 1, 1))
    finally:
        mc.fetch = orig
    eq([g["date"] for g in got], ["20260128", "20260318"], "둘째 날")
    print("test_fomc_takes_the_second_day_of_a_two_day_meeting: OK")


if __name__ == "__main__":
    test_screen_all_conditions_must_hold()
    test_screen_drops_unknown_values()
    test_screen_excludes_foreign_currency_and_halted()
    test_screen_ranks_by_pbr_over_roe_and_states_the_rule()
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
    test_news_second_group_skips_what_the_first_took()
    test_news_blanks_a_group_that_is_purely_a_duplicate()
    test_tree_backs_off_to_the_last_trading_day()
    test_report_deadlines_follow_the_law()
    test_quarter_label_matches_the_store_keys()
    test_calendar_leaves_meeting_dates_empty_when_it_cannot_fetch()
    test_fomc_takes_the_second_day_of_a_two_day_meeting()
    print("\nALL BOARD TESTS PASSED")
