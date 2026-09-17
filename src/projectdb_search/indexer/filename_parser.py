"""Regex/rule-based metadata extraction.

This is the ONE rule engine shared by both indexing (parsing filenames/folder
names) and search (parsing the user's natural-language query) — see
`search/query_parser.py`, which calls `parse_freetext()` directly. Keeping a
single rule table guarantees a query field only matches an index field when
both were derived the same way.

Patterns come from config/default_config.toml so they can be tuned as real
naming conventions are discovered, without touching code.
"""

from __future__ import annotations

import re
from pathlib import Path

from projectdb_search.config import AppConfig
from projectdb_search.models import PartialMetadata

# 가-힣 = the modern Hangul syllable block. Without it, Korean
# filenames/queries (e.g. "압축기_데이터시트_2020") tokenize to nothing at
# all -- every non-ASCII character is otherwise treated as a delimiter.
_TOKEN_SPLIT_RE = re.compile(r"[^a-zA-Z0-9&가-힣]+")
_STOPWORDS = {
    "the", "a", "an", "for", "of", "and", "or", "to", "in", "on", "at",
    "is", "was", "from", "with", "pdf", "jpg", "jpeg", "png", "tif", "tiff",
}


class FilenameParser:
    def __init__(self, config: AppConfig):
        self.config = config
        self._doc_type_patterns = self._compile_map(config.doc_type_patterns)
        self._project_name_patterns = self._compile_map(config.project_name_patterns)
        self._department_patterns = self._compile_map(config.department_patterns)
        self._equipment_tag_re = (
            re.compile(config.equipment_tag_pattern, re.IGNORECASE) if config.equipment_tag_pattern else None
        )
        self._year_re = re.compile(config.year_pattern) if config.year_pattern else None

    @staticmethod
    def _compile_map(patterns: dict[str, list[str]]) -> dict[str, list[re.Pattern]]:
        return {
            label: [re.compile(p, re.IGNORECASE) for p in pattern_list]
            for label, pattern_list in patterns.items()
        }

    def parse(self, corpus_root: Path, file_path: Path) -> PartialMetadata:
        """Parse a file's name + its parent folder names (index-time entry point)."""
        relative_parts = file_path.relative_to(corpus_root).parts
        text = " / ".join([file_path.stem, *relative_parts[:-1]])
        return self.parse_freetext(text)

    def parse_freetext(self, text: str) -> PartialMetadata:
        """Parse arbitrary text (a filename, folder path, PDF excerpt, or a
        user's search query) into structured fields. Shared by index-time and
        query-time callers so both use identical matching logic.
        """
        # Regex `\b` treats underscore as a word character, so
        # "HX-203_Isometric" would NOT get a boundary between "203" and "_"
        # for a pattern like `\bpid\b` or the equipment-tag regex. Filenames
        # routinely use "_" as a word separator, so normalize it to a space
        # before running any `\b`-anchored pattern. The tokenizer already
        # splits on "_" regardless, so this doesn't change `keywords`.
        normalized = text.replace("_", " ")
        return PartialMetadata(
            project_name=self._match_labeled(normalized, self._project_name_patterns),
            doc_type=self._match_labeled(normalized, self._doc_type_patterns),
            year=self._match_year(normalized),
            department=self._match_labeled(normalized, self._department_patterns),
            equipment_tag=self._match_equipment_tag(normalized),
            keywords=self._tokenize(text),
        )

    @staticmethod
    def _match_labeled(text: str, patterns: dict[str, list[re.Pattern]]) -> str | None:
        for label, compiled_list in patterns.items():
            for pattern in compiled_list:
                if pattern.search(text):
                    return label
        return None

    def _match_equipment_tag(self, text: str) -> str | None:
        if not self._equipment_tag_re:
            return None
        match = self._equipment_tag_re.search(text)
        return match.group(0).upper() if match else None

    def _match_year(self, text: str) -> int | None:
        if not self._year_re:
            return None
        match = self._year_re.search(text)
        return int(match.group(0)) if match else None

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        tokens = [t.lower() for t in _TOKEN_SPLIT_RE.split(text) if t]
        return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]
