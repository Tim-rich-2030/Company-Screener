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


def test_tiles_are_cut_but_aggregates_are_not():
    """
    타일은 시총 상위만 그리지만, 업종 집계와 상승/하락 종목 수는 전 종목으로
    낸다. 자른 뒤에 집계하면 '상승 109 / 하락 131' 이 대형주만의 이야기가 된다.
    """
    items = [{"code": str(i), "name": f"종목{i}", "market": "KOSPI",
              "sector": "화학" if i < 3 else "은행",
              "chg": 1.0 if i % 2 else -1.0,
              "cap": (10 - i) * 1_000_000_000_000, "close": 100}
             for i in range(6)]
    out = mt.build({"date": "20260731", "source": "pykrx", "items": items}, top_n=2)
    eq(out["breadth"], {"up": 3, "down": 3, "flat": 0, "total": 6},
       "상승/하락 집계는 전 종목")
    eq(len(out["sectors"]), 2, "업종도 전 종목 기준")
    eq(out["shown"], 2, "타일만 잘린다")
    eq([t["name"] for t in out["items"]], ["종목1", "종목0"],
       "시총 상위 2개를 뽑은 뒤 상승순")
    print("test_tiles_are_cut_but_aggregates_are_not: OK")


def test_sector_map_prefers_the_narrowest_index():
    """
    한 종목이 여러 업종지수에 들어가면 구성종목이 가장 적은 지수를 택한다.
    은행 ⊂ 금융업 일 때 '은행' 이 나와야 한다.
    """
    class FakeStock:
        idx = {"1021": ("금융업", ["A", "B", "C", "D"]),
               "1022": ("은행", ["A", "B"]),
               "1028": ("코스피200", ["A", "B", "C", "D"])}   # 업종 아님

        def get_index_ticker_list(self, date, market="KOSPI"):
            return list(self.idx)

        def get_index_ticker_name(self, t):
            return self.idx[t][0]

        def get_index_portfolio_deposit_file(self, t, date):
            return self.idx[t][1]

    got = mt.sector_map(FakeStock(), "20260731", "KOSPI")
    eq(got, {"A": "은행", "B": "은행", "C": "금융업", "D": "금융업"},
       "좁은 지수 우선, 크기·테마 지수는 제외")
    print("test_sector_map_prefers_the_narrowest_index: OK")


def test_sector_map_survives_a_broken_index():
    """지수 하나가 터져도 나머지는 분류된다. 업종은 있으면 좋은 것이지 필수가 아니다."""
    class Flaky:
        def get_index_ticker_list(self, date, market="KOSPI"):
            return ["1008", "9999"]

        def get_index_ticker_name(self, t):
            if t == "9999":
                raise RuntimeError("KRX 응답 없음")
            return "화학"

        def get_index_portfolio_deposit_file(self, t, date):
            return ["A"]

    eq(mt.sector_map(Flaky(), "20260731", "KOSPI"), {"A": "화학"}, "부분 실패")
    print("test_sector_map_survives_a_broken_index: OK")


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


def test_news_blanks_a_group_that_duplicates_another(monkey=None):
    """
    두 갈래가 똑같은 기사를 돌려주면 뒤쪽을 비운다.

    '주요뉴스' 와 '많이 본 뉴스' 에 같은 목록이 붙어 있으면, 빈 칸보다 나쁘다 —
    빈 칸은 고장으로 보이지만 같은 목록은 진짜 두 갈래로 읽힌다.
    """
    same = ('<a href="/news/news_read.naver?article_id=1&office_id=2">'
            '똑같은 기사 제목입니다</a>')
    orig_fetch, orig_feeds = mn.fetch, mn.FEEDS
    mn.fetch = lambda url: (same, "테스트")
    mn.FEEDS = [("주요뉴스", "a"), ("많이 본 뉴스", "b")]
    try:
        out = mn.collect()
    finally:
        mn.fetch, mn.FEEDS = orig_fetch, orig_feeds
    eq(len(out["groups"][0]["items"]), 1, "앞쪽은 남는다")
    eq(out["groups"][1]["items"], [], "뒤쪽은 비운다")
    eq(out["failed"], ["많이 본 뉴스"], "비운 갈래를 기록한다")
    print("test_news_blanks_a_group_that_duplicates_another: OK")


def test_tree_backs_off_to_the_last_trading_day():
    """
    휴장일에 돌리면 하루씩 물러선다.

    지수 수집기는 기간을 통째로 요청해서 알아서 마지막 거래일로 떨어지지만
    시장 지도는 하루만 묻는다. 일요일 실행에서 이것 때문에 통째로 실패했다.
    """
    import datetime as dt

    class Calendar:
        """20260731(금)까지만 시세가 있다."""
        def __init__(self):
            self.asked = []

        def get_market_ohlcv_by_ticker(self, date, market="KOSPI"):
            self.asked.append(date)
            class DF:
                def __init__(self, empty): self.empty = empty
            return DF(date > "20260731")

    cal = Calendar()
    got = mt.last_trading_day(cal, dt.date(2026, 8, 2))   # 일요일
    eq(got, "20260731", "금요일로 물러선다")
    eq(cal.asked, ["20260802", "20260801", "20260731"], "하루씩 거슬러 올라간다")

    class Dead:
        def get_market_ohlcv_by_ticker(self, date, market="KOSPI"):
            raise RuntimeError("KRX 접속 실패")

    eq(mt.last_trading_day(Dead(), dt.date(2026, 8, 2), back=3), None,
       "끝까지 못 찾으면 None — 엉뚱한 날짜를 지어내지 않는다")
    print("test_tree_backs_off_to_the_last_trading_day: OK")


if __name__ == "__main__":
    test_screen_all_conditions_must_hold()
    test_screen_drops_unknown_values()
    test_screen_excludes_foreign_currency_and_halted()
    test_screen_ranks_by_pbr_over_roe_and_states_the_rule()
    test_sector_change_is_cap_weighted()
    test_tiles_are_cut_but_aggregates_are_not()
    test_sector_map_prefers_the_narrowest_index()
    test_sector_map_survives_a_broken_index()
    test_news_parses_by_link_shape_not_class_name()
    test_news_returns_nothing_rather_than_garbage()
    test_news_dedupes_the_same_article_across_sections()
    test_news_decoding_ignores_a_lying_charset()
    test_news_prefers_the_title_attribute()
    test_news_blanks_a_group_that_duplicates_another()
    test_tree_backs_off_to_the_last_trading_day()
    print("\nALL BOARD TESTS PASSED")
