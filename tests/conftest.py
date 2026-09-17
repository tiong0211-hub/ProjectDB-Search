from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from projectdb_search.config import AppConfig, load_config
from projectdb_search.indexer.pipeline import run_pipeline
from projectdb_search.storage.inverted_index import InvertedIndex, load_inverted_index

_FIXTURES_MODULE_PATH = Path(__file__).parent / "fixtures" / "generate_fixtures.py"
_spec = importlib.util.spec_from_file_location("generate_fixtures", _FIXTURES_MODULE_PATH)
generate_fixtures = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(generate_fixtures)


@pytest.fixture(scope="session")
def app_config() -> AppConfig:
    return load_config()


@pytest.fixture
def corpus_root(tmp_path: Path) -> Path:
    root = tmp_path / "raw_docs"
    generate_fixtures.generate_into(root)
    return root


@pytest.fixture
def built_index(corpus_root: Path, tmp_path: Path, app_config: AppConfig) -> tuple[Path, InvertedIndex]:
    index_dir = tmp_path / "index"
    run_pipeline(corpus_root, index_dir, app_config)
    inverted = load_inverted_index(index_dir)
    assert inverted is not None
    return index_dir, inverted
