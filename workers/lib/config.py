"""config/ 파일 로더. 설정의 유일한 정의처는 config/ 디렉터리다 (hardcode 금지)."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
FIXTURES_DIR = REPO_ROOT / "fixtures"
SHARED_DIR = REPO_ROOT / "packages" / "shared"


@lru_cache(maxsize=None)
def sources_config() -> dict:
    return yaml.safe_load((CONFIG_DIR / "sources.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def seeds_config() -> dict:
    return yaml.safe_load((CONFIG_DIR / "seeds.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def scoring_config() -> dict:
    return json.loads((CONFIG_DIR / "scoring.v1.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def shared_constants() -> dict:
    return json.loads((SHARED_DIR / "constants.json").read_text(encoding="utf-8"))


def resolve_source_names(name_or_alias: str) -> list[str]:
    cfg = sources_config()
    aliases = cfg.get("aliases", {})
    if name_or_alias in aliases:
        return list(aliases[name_or_alias])
    known = {s["name"] for s in cfg["sources"]}
    if name_or_alias in known:
        return [name_or_alias]
    raise KeyError(f"unknown source or alias: {name_or_alias}")


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL 환경변수가 필요합니다")
    return url


def radar_mode() -> str:
    return os.environ.get("RADAR_MODE", "").lower()
