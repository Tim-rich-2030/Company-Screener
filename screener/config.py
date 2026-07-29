# -*- coding: utf-8 -*-
"""설정 — 여기만 고치면 됩니다."""
import os

# --- 감시 대상 -----------------------------------------------------------
CORP_CLS = "Y"                 # Y=유가증권(코스피), K=코스닥, N=코넥스, E=기타
MIN_MARKET_CAP_KRW = 500_000_000_000   # backfill --all 의 시총 하한 (5,000억).
                               # update 는 공시 감지 방식이라 시총 제한을 쓰지 않는다
WATCH_LOOKBACK_DAYS = 3        # 매 실행 시 며칠치 공시를 훑을지 (재실행 누락 방지용 여유)

# --- 저장 위치 -----------------------------------------------------------
STORE_DIR = os.environ.get("SCREENER_STORE", "store")
FACTS_DIR = os.path.join(STORE_DIR, "facts")      # 종목별 분기 원천 데이터
STATE_PATH = os.path.join(STORE_DIR, "state.json")  # 처리한 접수번호 등
SITE_DIR = os.environ.get("SCREENER_SITE", "docs")  # GitHub Pages 루트

# --- 수집 --------------------------------------------------------------
DART_WORKERS = 4
MAX_TICKERS_PER_RUN = 200      # 한 번 실행에서 처리할 종목 수 상한 (Actions 시간 제한 대비)
KEEP_QUARTERS = 24             # 종목당 보관할 분기 수 (6년)

# --- 지표 --------------------------------------------------------------
# 파생 지표는 저장하지 않고 사이트를 만들 때마다 다시 계산한다.
# 정의를 바꿨을 때 과거에 저장된 낡은 값이 남지 않게 하기 위함.
