"""정규화·중복제거의 순수 함수. 네트워크·DB 접근 없음 (unit test 대상)."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

_TAG_RE = re.compile(r"</?b>|</?strong>", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\"'“”‘’!?,.…·\[\]()<>|:;~`^#*_-]+")


def strip_markup(text: str) -> str:
    """Naver 검색 API가 검색어를 <b>로 감싸는 것 등 마크업 제거."""
    return _TAG_RE.sub("", text or "")


def normalize_text(text: str) -> str:
    t = strip_markup(text)
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip().lower()
    return t


def normalize_url(url: str | None) -> str | None:
    """dedupe용 URL 정규화: 쿼리스트링·프래그먼트 제거, 소문자 host."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path.rstrip("/"), "", ""))


def content_hash(title: str, excerpt: str | None) -> str:
    """동일 사건 재발행(신디케이션) 탐지용 해시. 제목+발췌 정규화 기반."""
    basis = normalize_text(title) + "\n" + normalize_text(excerpt or "")
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()
