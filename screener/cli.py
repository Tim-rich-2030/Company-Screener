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
    """
    과거 분기를 소급 수집한다.

    한 번에 전 종목을 돌리면 몇 시간이 걸려 Actions 시간 제한에 걸린다. 그래서
    --limit 만큼만 처리하고 어디까지 했는지 state 에 남긴다. 같은 명령을 반복
    실행하면 남은 종목이 이어서 채워진다.
    """
    client = _client()
    state = store.load_state()
    done = set(state.setdefault("backfilled", []))

    if args.codes:
        codes = [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()]
        names = {}
    elif args.all:
        market, _, source = base.fetch_market_snapshot(base.resolve_base_date(""))
        market = market[market["시가총액"] >= args.min_cap]
        market = market[~market["종목명"].map(base.is_preferred_name)]
        names = dict(zip(market["종목코드"], market["종목명"]))
        codes = [c for c in market["종목코드"] if c not in done]
        log(f"[소급] 대상 {len(market)}종목 중 미처리 {len(codes)}종목 "
            f"(완료 {len(done)}) · source={source}")
    else:
        raise SystemExit("--codes 005930,000660 또는 --all 중 하나를 지정하세요.")

    todo = codes[:args.limit] if args.limit else codes
    if not todo:
        log("[소급] 처리할 종목이 없습니다. 이미 전부 채워졌습니다.")
        store.save_state(state)     # store/ 가 없으면 뒤따르는 커밋 단계가 실패한다
        site.build()
        return 0

    corp_map = base.fetch_corp_code_map(client.api_key)
    periods = base.build_period_candidates(
        base.resolve_base_date(""), lookback_years=args.years)
    log(f"[소급] {len(todo)}종목 × 보고서 {len(periods)}건 "
        f"({periods[-1].label} ~ {periods[0].label})")

    def work(code):
        corp_code = corp_map.get(code)
        record = store.load(code)
        if not corp_code:
            return code, None, "DART corp_code 없음"
        result = ingest.backfill_one(
            client, corp_code, names.get(code, record.get("name", "")), periods, record)
        filled = prices.fill_quarter_prices(record)
        if record.get("quarters"):
            store.save(record)
        return code, {**result, "prices": filled,
                      "total": len(record.get("quarters", {}))}, ""

    ok, fail = 0, []
    bar = tqdm(total=len(todo), desc="소급", unit="종목")
    try:
        with ThreadPoolExecutor(max_workers=max(1, config.DART_WORKERS)) as pool:
            futures = [pool.submit(work, c) for c in todo]
            try:
                for fut in as_completed(futures):
                    code, result, err = fut.result()
                    if result is None:
                        fail.append(f"{code} ({err})")
                    else:
                        done.add(code)
                        ok += 1
                        if args.codes:      # 종목을 직접 지정했을 때만 상세 출력
                            log(f"  {code}: 분기 {len(result['quarters'])}개 채움, "
                                f"주가 {result['prices']}개 (누적 {result['total']}분기)")
                    bar.update(1)
            except (SystemExit, KeyboardInterrupt):
                for f in futures:
                    f.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
                raise
    finally:
        bar.close()
        state["backfilled"] = sorted(done)
        store.save_state(state)

    remaining = max(0, len(codes) - len(todo))
    log("")
    log(f"[소급] 완료 {ok}종목 / 실패 {len(fail)} / 남은 종목 {remaining}")
    for line in fail[:10]:
        log(f"  - {line}")
    if remaining:
        log(f"       같은 명령을 다시 실행하면 남은 {remaining}종목이 이어서 채워집니다.")
    log(f"       DART 호출 {client.calls}건")
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

    bf = sub.add_parser("backfill", help="과거 분기 소급 수집 (반복 실행하면 이어서 진행)")
    bf.add_argument("--codes", help="쉼표로 구분한 종목코드. 없으면 --all 필요")
    bf.add_argument("--all", action="store_true", help="시총 하한 이상 전 종목")
    bf.add_argument("--years", type=int, default=3, help="몇 년치를 훑을지 (기본 3년)")
    bf.add_argument("--limit", type=int, default=0,
                    help="이번 실행에서 처리할 종목 수 (0=제한 없음). 나머지는 다음 실행으로")
    bf.add_argument("--min-cap", type=int, default=config.MIN_MARKET_CAP_KRW,
                    dest="min_cap", help="--all 일 때 시가총액 하한(원)")
    bf.set_defaults(func=cmd_backfill)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
