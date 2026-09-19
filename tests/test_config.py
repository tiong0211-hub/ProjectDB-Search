from __future__ import annotations

from projectdb_search.config import load_config


def test_direct_open_extensions_default():
    config = load_config()
    assert config.extraction.direct_open_extensions == {
        ".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".hwpx",
    }
