# -*- coding: utf-8 -*-
"""
자동 실행 진입점.

    python -m screener update     공시 감지 -> 증분 수집 -> 주가 채우기 -> 사이트
    python -m screener site       사이트만 다시 생성 (지표 바꿨을 때, 수 초)
    python -m screener backfill --codes 005930,000660   특정 종목 과거 소급

GitHub Actions 가 `update` 를 주기적으로 돌리고 결과를 저장소에 커밋한다.
"""
from __future__ import annotations

import sys
import argparse
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import kospi_value_screener as base
from kospi_value_screener import log, DartClient, get_api_key, tqdm

from . import config, store, watch, ingest, prices, site


def _client() -> DartClient:
    return DartClient(get_api_key())


def cmd_update(args) -> int:
    client = _client()
    state = store.load_state()
    seen = set(state["seen_rcept"])

    hits = watch.detect(client, seen, days=args.days)
    # 지난 실행에서 시간이 모자라 못 처리한 것들을 먼저 소진한다
    pending = [h for h in state.get("pending", []) if h.get("rcept_no") not in seen]
    queue = pending + [h for h in hits
                       if h["rcept_no"] not in {p.get("rcept_no") for p in pending}]

    log(f"[감지] 신규 정기보고서 {len(hits)}건 / 이월 {len(pending)}건 "
        f"-> 처리 대상 {len(queue)}건")
    if not queue:
        log("      새 실적 없음 — 사이트만 갱신합니다.")

    todo, defer = queue[:config.MAX_TICKERS_PER_RUN], queue[config.MAX_TICKERS_PER_RUN:]
    if defer:
        log(f"      이번 실행 상한({config.MAX_TICKERS_PER_RUN}) 초과분 "
            f"{len(defer)}건은 다음 실행으로 넘깁니다.")

    updated, failed = [], []

    def work(hit):
        record = store.load(hit["code"])
        result = ingest.ingest_one(client, hit, record)
        if result["changed"]:
            prices.fill_quarter_prices(record)
            store.save(record)
        return hit, result

    if todo:
        bar = tqdm(total=len(todo), desc="수집", unit="종목")
        try:
            with ThreadPoolExecutor(max_workers=max(1, config.DART_WORKERS)) as pool:
                futures = [pool.submit(work, h) for h in todo]
                try:
                    for fut in as_completed(futures):
                        hit, result = fut.result()
                        seen.add(hit["rcept_no"])
                        (updated if result["changed"] else failed).append(
                            f"{hit['code']} {hit['name']} {hit['qkey']}"
                            + ("" if result["changed"] else f" ({result['note']})"))
                        bar.update(1)
                except (SystemExit, KeyboardInterrupt):
                    for f in futures:
                        f.cancel()
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
        finally:
            bar.close()

    state["seen_rcept"] = sorted(seen)
    state["pending"] = defer
    state["last_run"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.save_state(state)

    path = site.build()
    log("")
    log("=" * 66)
    log(f"갱신 종목 : {len(updated)}")
    for line in updated[:20]:
        log(f"  + {line}")
    if len(updated) > 20:
        log(f"  ... 외 {len(updated) - 20}건")
    if failed:
        log(f"수집 실패 : {len(failed)}")
        for line in failed[:10]:
            log(f"  - {line}")
    log(f"DART 호출 : {client.calls}")
    log(f"사이트     : {path}")
    log("=" * 66)
    return 0


def cmd_site(args) -> int:
    path = site.build()
    log(f"[사이트] {path}")
    return 0


def cmd_backfill(args) -> int:
    """특정 종목의 과거 분기를 소급 수집한다 (최초 1회용)."""
    client = _client()
    codes = [c.strip().zfill(6) for c in (args.codes or "").split(",") if c.strip()]
    if not codes:
        raise SystemExit("--codes 005930,000660 형식으로 종목을 지정하세요.")

    corp_map = base.fetch_corp_code_map(client.api_key)
    periods = base.build_period_candidates(
        base.resolve_base_date(""), lookback_years=args.years)

    for code in codes:
        corp_code = corp_map.get(code)
        if not corp_code:
            log(f"[소급] {code}: DART corp_code 없음 — 건너뜁니다")
            continue
        record = store.load(code)
        touched = []
        for period in periods:
            quarter = ingest.QUARTER_TO_REPRT
            qnum = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}.get(period.reprt_code)
            if qnum is None:
                continue
            hit = {"corp_code": corp_code, "code": code,
                   "name": record.get("name", ""), "year": period.year,
                   "reprt_code": period.reprt_code, "quarter": qnum,
                   "rcept_no": "", "report_nm": period.label}
            result = ingest.ingest_one(client, hit, record)
            touched += result["quarters"]
        filled = prices.fill_quarter_prices(record)
        store.save(record)
        log(f"[소급] {code} {record.get('name','')}: 분기 {len(set(touched))}개, "
            f"주가 {filled}개 채움 (누적 {len(record['quarters'])}분기)")

    site.build()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="screener", description="코스피 분기 실적 시계열")
    sub = p.add_subparsers(dest="cmd")

    up = sub.add_parser("update", help="공시 감지 -> 수집 -> 사이트")
    up.add_argument("--days", type=int, default=config.WATCH_LOOKBACK_DAYS,
                    help="며칠치 공시를 훑을지")
    up.set_defaults(func=cmd_update)

    st = sub.add_parser("site", help="사이트만 다시 생성")
    st.set_defaults(func=cmd_site)

    bf = sub.add_parser("backfill", help="특정 종목 과거 소급 수집")
    bf.add_argument("--codes", required=True, help="쉼표로 구분한 종목코드")
    bf.add_argument("--years", type=int, default=3, help="몇 년치를 훑을지")
    bf.set_defaults(func=cmd_backfill)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
