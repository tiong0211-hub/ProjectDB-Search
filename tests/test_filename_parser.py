from __future__ import annotations

from pathlib import Path

from projectdb_search.config import AppConfig
from projectdb_search.indexer.filename_parser import FilenameParser


def test_doc_type_and_project_matched_from_folder_names(app_config: AppConfig, tmp_path: Path):
    parser = FilenameParser(app_config)
    corpus_root = tmp_path
    file_path = corpus_root / "RiversidePlant" / "PID" / "RiversidePlant_PID_Unit3_2021.pdf"
    file_path.parent.mkdir(parents=True)
    file_path.touch()

    meta = parser.parse(corpus_root, file_path)

    assert meta.doc_type == "P&ID"
    assert meta.project_name == "Riverside Plant"
    assert meta.year == 2021


def test_equipment_tag_matches_despite_underscore_adjacency(app_config: AppConfig):
    parser = FilenameParser(app_config)
    meta = parser.parse_freetext("HX-203_Isometric_Rev2")
    assert meta.equipment_tag == "HX-203"
    assert meta.doc_type == "isometric"


def test_equipment_tag_is_case_insensitive_but_normalized_uppercase(app_config: AppConfig):
    parser = FilenameParser(app_config)
    meta = parser.parse_freetext("looking for hx-203 isometric")
    assert meta.equipment_tag == "HX-203"


def test_non_conforming_filename_yields_mostly_empty_metadata(app_config: AppConfig, tmp_path: Path):
    parser = FilenameParser(app_config)
    file_path = tmp_path / "Legacy" / "blurry_scan_noname.pdf"
    file_path.parent.mkdir(parents=True)
    file_path.touch()

    meta = parser.parse(tmp_path, file_path)

    assert meta.project_name is None
    assert meta.doc_type is None
    assert meta.equipment_tag is None
    # Still tokenized, so the document remains findable by generic keywords.
    assert "legacy" in meta.keywords
    assert "blurry" in meta.keywords


def test_stopwords_and_short_tokens_are_dropped(app_config: AppConfig):
    parser = FilenameParser(app_config)
    meta = parser.parse_freetext("memo for the unit 3 review")
    assert "for" not in meta.keywords
    assert "the" not in meta.keywords
    assert "3" not in meta.keywords  # single-char tokens filtered
    assert "unit" in meta.keywords
    assert "review" in meta.keywords
