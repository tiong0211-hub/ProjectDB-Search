"""Loads config/default_config.toml (regex patterns, ranking weights, LLM settings).

Patterns live in TOML rather than in Python so they can be tuned once a real
document corpus is available, without a code change/redeploy.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "default_config.toml"


@dataclass
class RankingConfig:
    weights: dict[str, float]
    ambiguous_absolute_threshold: float
    ambiguous_margin: float
    max_keyword_hits: int


@dataclass
class LLMConfig:
    api_key: str
    base_url: str
    model: str


@dataclass
class ExtractionConfig:
    min_text_chars: int
    ocr_dpi: int
    ocr_confidence_threshold: float
    pdf_extensions: set[str]
    image_extensions: set[str]
    max_fallback_pages: int
    ocr_max_side_px: int
    deep_scan_workers: int
    direct_open_extensions: set[str]


@dataclass
class AppConfig:
    doc_type_patterns: dict[str, list[str]]
    project_name_patterns: dict[str, list[str]]
    department_patterns: dict[str, list[str]]
    equipment_tag_pattern: str
    year_pattern: str
    ranking: RankingConfig
    llm: LLMConfig
    extraction: ExtractionConfig
    raw: dict = field(default_factory=dict)


def load_config(path: Path | None = None) -> AppConfig:
    path = path or DEFAULT_CONFIG_PATH
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    ranking_raw = raw.get("ranking", {})
    llm_raw = raw.get("llm", {})
    extraction_raw = raw.get("extraction", {})

    return AppConfig(
        doc_type_patterns=raw.get("doc_type_patterns", {}),
        project_name_patterns=raw.get("project_name_patterns", {}),
        department_patterns=raw.get("department_patterns", {}),
        equipment_tag_pattern=raw.get("equipment_tag_pattern", {}).get("regex", ""),
        year_pattern=raw.get("year_pattern", {}).get("regex", ""),
        ranking=RankingConfig(
            weights=raw.get("ranking_weights", {}),
            ambiguous_absolute_threshold=ranking_raw.get("ambiguous_absolute_threshold", 20),
            ambiguous_margin=ranking_raw.get("ambiguous_margin", 5),
            max_keyword_hits=ranking_raw.get("max_keyword_hits", 5),
        ),
        llm=LLMConfig(
            api_key=llm_raw.get("api_key", ""),
            base_url=llm_raw.get("base_url", ""),
            model=llm_raw.get("model", ""),
        ),
        extraction=ExtractionConfig(
            min_text_chars=extraction_raw.get("min_text_chars", 20),
            ocr_dpi=extraction_raw.get("ocr_dpi", 200),
            ocr_confidence_threshold=extraction_raw.get("ocr_confidence_threshold", 0.55),
            pdf_extensions=set(extraction_raw.get("pdf_extensions", [".pdf"])),
            image_extensions=set(
                extraction_raw.get("image_extensions", [".jpg", ".jpeg", ".png", ".tif", ".tiff"])
            ),
            max_fallback_pages=extraction_raw.get("max_fallback_pages", 2),
            ocr_max_side_px=extraction_raw.get("ocr_max_side_px", 2400),
            deep_scan_workers=extraction_raw.get("deep_scan_workers", 0),
            direct_open_extensions=set(
                extraction_raw.get(
                    "direct_open_extensions", [".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".hwpx"]
                )
            ),
        ),
        raw=raw,
    )
