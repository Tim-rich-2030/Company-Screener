# -*- coding: utf-8 -*-
"""
live 가지에 올릴 것을 고른다 — **더 새것만.**

왜 필요한가
===========

장 끝난 뒤에 '실시간 종목'이 어제 등락을 보여줬다. 삼성전기가 그날 9% 가까이
빠졌는데 화면에는 +14.43% 로 떠 있었다. 어제 오른 폭이었다.

값이 틀린 게 아니라 **어제 것이 오늘 것을 덮은 것**이었다. 반복문이 이렇게
생겼기 때문이다.

    git reset --hard FETCH_HEAD     # docs/*.json ← main 사본 (하루 한 번 것)
    ... 장중이면 종목·지도를 다시 만든다 ...
    cp docs/*.json  →  live 로 force-push

장 시간이 아니면 가운데 단계를 건너뛴다. 그러면 git reset 이 되돌려 놓은
**어제 사본**이 그대로 live 로 올라가, 오늘 장중에 만든 것을 지운다.
5분마다, 장이 닫힌 내내.

무엇을 고치는가
==============

올릴 파일을 그때그때 고르지 않는다. **지금 live 에 있는 것보다 새것일 때만**
올린다. 판단 기준은 화면이 쓰는 것과 같다 (index.html 의 stamp/newer).

    fetched_at → intraday_at → date 순으로 보고, 숫자만 남겨 14자리로 채운다

14자리로 채우는 것이 중요하다. 날짜만 있는 "20260806" 을 그대로 두면
"20260806062101" 보다 짧아 사전순으로 앞선다 — 같은 날 아침에 만든 것이
오후에 만든 것보다 새것으로 읽힌다. 0 으로 채우면 20260806000000 이 되어
자연스레 그날 자정으로 놓인다.

    python live_publish.py <live 의 docs 경로> <파일> [<파일> ...]
"""
from __future__ import annotations

import os
import re
import sys
import json
import shutil

# 화면(index.html 의 stamp)과 같은 순서로 본다. 한쪽만 고치면 화면과 live 가
# 서로 다른 것을 새것이라고 부르게 된다.
TIME_KEYS = ("fetched_at", "intraday_at", "date")


def stamp(payload) -> str:
    """{...} → '20260806062101'. 못 읽으면 빈 글자."""
    if not isinstance(payload, dict):
        return ""
    for k in TIME_KEYS:
        v = payload.get(k)
        if v:
            digits = re.sub(r"\D", "", str(v))[:14]
            if digits:
                return digits.ljust(14, "0")
    return ""


def read(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def decide(src: str, dst: str) -> tuple[bool, str]:
    """올릴지 말지와 그 이유. 저장은 하지 않는다."""
    new = read(src)
    if new is None:
        return False, "만들어진 것이 없습니다"
    a = stamp(new)
    if not a:
        # 시각을 못 읽으면 올린다. 여기서 막으면 시각을 안 남기는 수집기가
        # 조용히 영영 안 올라간다 — 그 편이 덮어쓰는 것보다 알아채기 어렵다.
        return True, "시각을 못 읽어 그대로 올립니다"
    b = stamp(read(dst))
    if not b:
        return True, f"live 에 없던 것 ({a})"
    if a > b:
        return True, f"{b} → {a}"
    if a == b:
        return False, f"같은 것 ({a})"
    return False, f"live 것이 더 새것입니다 ({b} > {a}) — 두고 갑니다"


def publish(dest: str, files) -> int:
    os.makedirs(dest, exist_ok=True)
    put = 0
    for src in files:
        name = os.path.basename(src)
        dst = os.path.join(dest, name)
        ok, why = decide(src, dst)
        print(f"  {'올림' if ok else '건너뜀'}  {name:26} {why}", flush=True)
        if ok:
            shutil.copyfile(src, dst)
            put += 1
    print(f"[live] {put}/{len(files)}개를 올립니다", flush=True)
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print("사용법: python live_publish.py <live/docs> <파일> [<파일> ...]")
        return 2
    return publish(argv[0], argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
