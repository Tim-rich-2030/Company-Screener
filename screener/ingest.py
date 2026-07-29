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
    for item in payload.get("list", []):
        se = base.norm_name(item.get("se"))
        if se.startswith("보통주"):
            val = base.to_num(item.get("isu_stock_totqy"))
            if val and val > 0:
                return val
    # 종류별 구분이 없으면 합계라도 쓴다
    for item in payload.get("list", []):
        if base.norm_name(item.get("se")).startswith("합계"):
            val = base.to_num(item.get("isu_stock_totqy"))
            if val and val > 0:
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
