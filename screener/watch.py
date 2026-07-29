# -*- coding: utf-8 -*-
"""
공시 감시 — "방금 실적을 낸 회사"만 골라낸다.

전 상장사를 매번 훑는 대신, DART 공시검색(list.json)으로 최근 제출된 정기보고서
목록을 받아 그 회사만 조회 대상에 넣는다. 호출 한 번으로 100건씩 받으므로
하루치 감지에 몇 번이면 끝난다.

뉴스 스크래핑 대신 이 방식을 쓰는 이유
--------------------------------------
* 뉴스는 기업명이 들어갔다고 실적 기사인지 알 수 없다(오탐). 공시는 보고서 종류가
  구조화돼 있어 오탐이 없다.
* 기사가 났다고 DART에 숫자가 올라와 있다는 보장이 없다. 공시 감지는 감지 시점에
  숫자가 반드시 존재한다.
* 포털 스크래핑은 차단·마크업 변경으로 언제든 조용히 멈춘다.
* 잠정실적(거래소 공정공시)도 같은 API로 잡힌다 — 정기보고서보다 2~4주 빠르다.
"""
from __future__ import annotations

import re
import datetime as dt

from . import config
from .store import quarter_key

# 정기공시. A001 사업보고서 / A002 반기보고서 / A003 분기보고서
PBLNTF_TY_PERIODIC = "A"

# 보고서명 예: "분기보고서 (2025.09)", "반기보고서 (2025.06)", "사업보고서 (2025.12)"
REPORT_RE = re.compile(r"(사업보고서|반기보고서|분기보고서)\s*\((\d{4})\.(\d{2})\)")

# 결산월이 12월인 회사 기준. 그 외는 조회 단계에서 두 코드를 모두 시도한다.
MONTH_TO_REPRT = {"03": "11013", "06": "11012", "09": "11014", "12": "11011"}
REPRT_TO_QUARTER = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}


def parse_report_name(report_nm: str) -> dict | None:
    """
    보고서명에서 사업연도와 보고서 코드를 뽑는다.

    반환: {"year": 2025, "reprt_code": "11014", "quarter": 3, "kind": "분기보고서"}
    정기보고서가 아니면 None. '[기재정정]' 같은 접두어가 붙어도 인식한다.
    """
    m = REPORT_RE.search(report_nm or "")
    if not m:
        return None
    kind, year, month = m.group(1), int(m.group(2)), m.group(3)

    if kind == "사업보고서":
        reprt = "11011"
    elif kind == "반기보고서":
        reprt = "11012"
    else:                                   # 분기보고서 — 월로 1·3분기를 가른다
        reprt = MONTH_TO_REPRT.get(month, "11013")
        if reprt not in ("11013", "11014"):
            reprt = "11013"
    return {"year": year, "reprt_code": reprt,
            "quarter": REPRT_TO_QUARTER[reprt], "kind": kind,
            "period_month": month}


def alt_reprt_code(reprt_code: str) -> str | None:
    """결산월이 12월이 아닌 회사를 위한 대체 코드 (1분기 <-> 3분기)."""
    return {"11013": "11014", "11014": "11013"}.get(reprt_code)


def fetch_disclosures(client, bgn_de: str, end_de: str,
                      corp_cls: str = None, max_pages: int = 30) -> list:
    """
    기간 내 정기보고서 공시 목록. 페이지를 끝까지 넘긴다.
    client 는 kospi_value_screener.DartClient.
    """
    corp_cls = config.CORP_CLS if corp_cls is None else corp_cls
    out, page = [], 1
    while page <= max_pages:
        params = {
            "bgn_de": bgn_de, "end_de": end_de,
            "pblntf_ty": PBLNTF_TY_PERIODIC,
            "page_no": str(page), "page_count": "100",
        }
        if corp_cls:
            params["corp_cls"] = corp_cls
        payload = client.get_json("list.json", params)
        if payload is None:
            break
        status = payload.get("status")
        if status == "013":                 # 해당 기간에 공시 없음 — 정상
            break
        if status != "000":
            from kospi_value_screener import DART_FATAL_STATUS
            if status in DART_FATAL_STATUS:
                raise SystemExit(
                    f"DART 오류 {status}: {DART_FATAL_STATUS[status]} — 중단합니다.")
            break
        out.extend(payload.get("list", []))
        if page >= int(payload.get("total_page") or 1):
            break
        page += 1
    return out


def detect(client, seen: set, days: int = None, today: dt.date = None) -> list:
    """
    최근 며칠치 공시에서 '아직 처리하지 않은 정기보고서'만 골라낸다.

    반환: [{"code","corp_code","name","year","reprt_code","quarter","rcept_no","rcept_dt"}]
    같은 회사·같은 분기가 여러 번(원본 + 정정) 나오면 최신 접수번호 하나만 남긴다.
    """
    days = config.WATCH_LOOKBACK_DAYS if days is None else days
    today = today or dt.date.today()
    bgn = (today - dt.timedelta(days=max(0, days - 1))).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    hits: dict[tuple, dict] = {}
    for item in fetch_disclosures(client, bgn, end):
        rcept_no = (item.get("rcept_no") or "").strip()
        stock_code = (item.get("stock_code") or "").strip()
        if not rcept_no or rcept_no in seen:
            continue
        if not stock_code or stock_code == " ":
            continue                        # 비상장 법인
        parsed = parse_report_name(item.get("report_nm"))
        if not parsed:
            continue
        key = (stock_code, parsed["year"], parsed["reprt_code"])
        prev = hits.get(key)
        if prev and prev["rcept_no"] >= rcept_no:
            continue                        # 접수번호가 큰 쪽(정정본)을 남긴다
        hits[key] = {
            "code": stock_code.zfill(6),
            "corp_code": (item.get("corp_code") or "").strip(),
            "name": (item.get("corp_name") or "").strip(),
            "rcept_no": rcept_no,
            "rcept_dt": (item.get("rcept_dt") or "").strip(),
            "report_nm": (item.get("report_nm") or "").strip(),
            "qkey": quarter_key(parsed["year"], parsed["quarter"]),
            **parsed,
        }
    return sorted(hits.values(), key=lambda h: h["rcept_no"])
