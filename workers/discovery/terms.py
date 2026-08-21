"""Term 추출 (M1: 공백 토큰 기반 1~4 gram).

M2에서 Kiwi 형태소 분석으로 교체 예정 (docs/ARCHITECTURE.md §3).
순수 함수 — unit test 대상.
"""
from __future__ import annotations

import re

from .normalize import normalize_text

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")

# 단독으로는 term이 되지 않는 일반어 (M1 최소 목록)
STOPWORDS = {
    "출시", "공개", "발표", "인기", "화제", "추천", "후기", "리뷰", "정리",
    "방법", "소식", "관련", "오늘", "최근", "신규", "확대", "발의", "시행",
    "그리고", "하지만", "있는", "있다", "합니다", "위한", "대한", "및",
    "분석", "상향", "비교", "이슈", "기사", "공유", "질문", "안내", "보도",
    "동향", "소감", "요약", "목록", "기능", "영상", "예정", "확인", "논의",
}

MAX_NGRAM = 4
MIN_TOKEN_LEN = 2


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(normalize_text(text)) if len(t) >= MIN_TOKEN_LEN]


def extract_ngrams(text: str, max_n: int = MAX_NGRAM) -> list[str]:
    """1~4 gram 후보. 전부 stopword인 n-gram과 stopword 단독은 제외."""
    tokens = tokenize(text)
    out: list[str] = []
    seen: set[str] = set()
    for n in range(1, max_n + 1):
        for i in range(len(tokens) - n + 1):
            gram_tokens = tokens[i : i + n]
            if all(t in STOPWORDS for t in gram_tokens):
                continue
            if n == 1 and gram_tokens[0] in STOPWORDS:
                continue
            gram = " ".join(gram_tokens)
            if gram not in seen:
                seen.add(gram)
                out.append(gram)
    return out


def dedupe_overlapping_terms(
    term_docs: dict[str, set[str]], overlap_threshold: float = 0.8
) -> list[str]:
    """겹치는 n-gram 정리: 더 긴 term이 짧은 term의 문서 대부분을 커버하면
    짧은 쪽을 버린다 ('오즈모포켓4' vs '오즈모포켓4 축구촬영').

    반환: 채택된 term 목록 (문서 수 내림차순 → 길이 내림차순 우선).
    """
    ordered = sorted(
        term_docs.items(),
        key=lambda kv: (len(kv[1]), len(kv[0].split()), len(kv[0])),
        reverse=True,
    )
    accepted: list[str] = []
    for term, docs in ordered:
        subsumed = False
        for a in accepted:
            if term == a:
                continue
            if (term in a or a in term) and docs:
                overlap = len(docs & term_docs[a]) / len(docs)
                if overlap >= overlap_threshold:
                    subsumed = True
                    break
        if not subsumed:
            accepted.append(term)
    return accepted
