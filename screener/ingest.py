# -*- coding: utf-8 -*-
"""
증분 수집 — 감지된 종목의 그 분기 숫자만 DART에서 가져온다.

전수조사가 아니라 공시가 뜬 종목만 건드리므로, 한 번 실행에 보통 수십 종목이다.
종목당 호출은 3~6건(해당 분기 보고서 + 차분용 직전 분기 + 주식총수).
"""
from __future__ import annotations

import kospi_value_screener as base
import quarterly_dashboard as qd

from . import config
from .store import quarter_key
from .watch import alt_reprt_code

QUARTER_TO_REPRT = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}


def _fetch_report(client, corp_code: str, year: int, reprt_code: str,
                  prefer: str = "CFS") -> tuple[dict | None, str]:
    """정상 응답과 사용한 재무제표 구분을 돌려준다."""
    for fs_div in ([prefer] + [d for d in ("CFS", "OFS") if d != prefer]):
        payload = client.get_json("fnlttSinglAcntAll.json", {
            "corp_code": corp_code, "bsns_year": str(year),
            "reprt_code": reprt_code, "fs_div": fs_div,
        })
        if payload is None:
            continue
        status = payload.get("status")
        if status in base.DART_FATAL_STATUS:
            raise SystemExit(
                f"DART 오류 {status}: {base.DART_FATAL_STATUS[status]} — 중단합니다.")
        if status == "000":
            return payload, fs_div
    return None, ""


# 주식총수현황 응답의 필드는 이름이 헷갈린다.
#   isu_stock_totqy      발행'할' 주식의 총수  = 정관상 수권주식수. 시총 계산에 쓰면 안 된다
#                        (삼성전자 200억주 — 실제 발행 59.7억주의 3.3배)
#   istc_totqy           발행주식의 총수      = 실제로 발행된 주식 수. 이게 맞다
#   distb_stock_co       유통주식수           = 발행주식 - 자기주식
# 시가총액은 상장주식수(=발행주식총수) 기준이므로 istc_totqy 를 쓴다.
SHARE_FIELDS = ("istc_totqy", "distb_stock_co")


def _shares_from_item(item: dict) -> float | None:
    for field in SHARE_FIELDS:
        val = base.to_num(item.get(field))
        if val and val > 0:
            return val
    return None


def fetch_shares(client, corp_code: str, year: int, reprt_code: str) -> float | None:
    """
    그 보고서 시점의 보통주 발행주식총수.

    과거 시가총액을 현재 주식수로 계산하면 증자·감자·액면분할이 있었던 종목에서
    통째로 틀린다. 그래서 분기마다 그 시점 주식수를 따로 받는다.
    """
    payload = client.get_json("stockTotqySttus.json", {
        "corp_code": corp_code, "bsns_year": str(year), "reprt_code": reprt_code,
    })
    if payload is None or payload.get("status") != "000":
        return None
    items = payload.get("list", [])
    for item in items:                      # 보통주 우선
        if base.norm_name(item.get("se")).startswith("보통주"):
            val = _shares_from_item(item)
            if val:
                return val
    for item in items:                      # 종류 구분이 없으면 합계
        if base.norm_name(item.get("se")).startswith("합계"):
            val = _shares_from_item(item)
            if val:
                return val
    return None


def ingest_one(client, hit: dict, record: dict) -> dict:
    """
    한 건의 공시를 받아 record(종목 시계열)에 반영한다.
    반환: {"changed": bool, "quarters": [반영된 분기], "note": str}
    """
    from .store import merge_quarter

    corp_code, year, quarter = hit["corp_code"], hit["year"], hit["quarter"]
    reprt_code = hit["reprt_code"]

    payload, fs_div = _fetch_report(client, corp_code, year, reprt_code)
    if payload is None:
        # 결산월이 12월이 아니면 1분기/3분기 코드가 뒤바뀐다 — 반대쪽으로 재시도
        alt = alt_reprt_code(reprt_code)
        if alt:
            payload, fs_div = _fetch_report(client, corp_code, year, alt)
            if payload is not None:
                reprt_code = alt
                quarter = {"11013": 1, "11014": 3}[alt]
    if payload is None:
        return {"changed": False, "quarters": [], "note": "보고서 조회 실패"}

    # 누계 수집: 이번 분기 + 차분에 필요한 직전 분기
    cums = dict(qd.parse_cumulative_is(payload, year, quarter))
    if quarter > 1:
        prev_payload, _ = _fetch_report(
            client, corp_code, year, QUARTER_TO_REPRT[quarter - 1], fs_div or "CFS")
        if prev_payload is not None:
            for key, vals in qd.parse_cumulative_is(prev_payload, year, quarter - 1).items():
                cums.setdefault(key, {}).update(
                    {k: v for k, v in vals.items() if k not in cums.get(key, {})})

    quarterly = qd.cumulative_to_quarterly(cums)

    # 재무상태표는 이번 보고서 시점의 잔액 -> 이번 분기에만 붙인다
    balance = qd.parse_balance_accounts(payload)
    shares = fetch_shares(client, corp_code, year, reprt_code)

    record["name"] = hit.get("name") or record.get("name", "")
    meta = {"rcept_no": hit.get("rcept_no", ""), "fs_div": fs_div,
            "report_nm": hit.get("report_nm", "")}

    touched = []
    for (yy, qq), vals in quarterly.items():
        qkey = quarter_key(yy, qq)
        payload_vals = dict(vals)
        if (yy, qq) == (year, quarter):
            payload_vals.update(balance)
            if shares:
                payload_vals["상장주식수"] = shares
        if merge_quarter(record, qkey, payload_vals, meta if (yy, qq) == (year, quarter) else None):
            touched.append(qkey)

    note = "" if touched else "새로 채운 값 없음"
    return {"changed": bool(touched), "quarters": sorted(touched, reverse=True), "note": note}


def backfill_one(client, corp_code: str, name: str, periods: list, record: dict) -> dict:
    """
    과거 여러 분기를 한꺼번에 채운다.

    ingest_one 을 기간마다 부르면 차분용 직전 분기를 매번 다시 받게 되어 호출이
    두 배가 된다. 여기서는 각 보고서를 **한 번씩만** 받아 누계를 모두 모은 뒤
    마지막에 한 번에 차분한다.

    재무상태표와 주식수는 보고서마다 그 시점 값이므로 각자의 분기에 붙인다.
    (최신 것 하나로 과거 PBR을 계산하면 시계열이 아니라 착시가 된다.)
    """
    from .store import merge_quarter

    cums: dict = {}
    balances: dict = {}
    shares: dict = {}
    meta: dict = {}
    fs_pref = "CFS"
    fetched = 0

    for period in periods:
        quarter = qd.QUARTER_OF_REPRT.get(period.reprt_code)
        if quarter is None:
            continue
        payload, fs_div = _fetch_report(client, corp_code, period.year,
                                        period.reprt_code, fs_pref)
        if payload is None:
            continue
        fetched += 1
        fs_pref = fs_div or fs_pref

        for key, vals in qd.parse_cumulative_is(payload, period.year, quarter).items():
            cums.setdefault(key, {}).update(
                {k: v for k, v in vals.items() if k not in cums.get(key, {})})

        bs = qd.parse_balance_accounts(payload)
        if bs:
            balances[(period.year, quarter)] = bs
        got_shares = fetch_shares(client, corp_code, period.year, period.reprt_code)
        if got_shares:
            shares[(period.year, quarter)] = got_shares
        meta[(period.year, quarter)] = {"fs_div": fs_div, "report_nm": period.label}

    if name:
        record["name"] = name

    touched = []
    for (yy, qq), vals in qd.cumulative_to_quarterly(cums).items():
        values = dict(vals)
        values.update(balances.get((yy, qq), {}))
        if (yy, qq) in shares:
            values["상장주식수"] = shares[(yy, qq)]
        if merge_quarter(record, quarter_key(yy, qq), values, meta.get((yy, qq))):
            touched.append(quarter_key(yy, qq))

    # 재무상태표만 있고 손익 차분이 안 된 분기(가장 오래된 쪽)도 살려둔다.
    # PBR 은 자기자본만 있으면 계산되므로 버릴 이유가 없다.
    for (yy, qq), bs in balances.items():
        qkey = quarter_key(yy, qq)
        values = dict(bs)
        if (yy, qq) in shares:
            values["상장주식수"] = shares[(yy, qq)]
        if merge_quarter(record, qkey, values, meta.get((yy, qq))) and qkey not in touched:
            touched.append(qkey)

    return {"fetched": fetched, "quarters": sorted(set(touched), reverse=True)}
