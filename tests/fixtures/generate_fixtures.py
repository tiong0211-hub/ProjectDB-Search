"""Creates a small synthetic document corpus under tests/fixtures/raw_docs/.

PDF/OCR text extraction isn't implemented yet (Phase 4), so filename/folder
naming is all that matters here — file contents are empty placeholders.
Run directly: `python tests/fixtures/generate_fixtures.py`.
"""

from __future__ import annotations

from pathlib import Path

RAW_DOCS_DIR = Path(__file__).parent / "raw_docs"

# (relative path under raw_docs/) — content is irrelevant in this phase.
FIXTURE_PATHS = [
    "RiversidePlant/PID/RiversidePlant_PID_Unit3_2021.pdf",
    "RiversidePlant/Isometric/HX-203_Isometric_Rev2.pdf",
    "RiversidePlant/Datasheet/TK-14_Datasheet_2019.pdf",
    "RiversidePlant/Memo/RiversidePlant_Memo_2020_SafetyReview.pdf",
    "CompressorStation/Memo/old_backup_copy_final_v2.pdf",
    "CompressorStation/Electrical/IMG_20190304_Electrical_MotorRoom.jpg",
    "CompressorStation/PID/CompressorStation_PID_Unit1_2018.pdf",
    "Legacy/blurry_scan_noname.pdf",
]


def generate_into(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for relative in FIXTURE_PATHS:
        path = target_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4\n% placeholder fixture, no real content\n")


def generate() -> None:
    generate_into(RAW_DOCS_DIR)
    print(f"Generated {len(FIXTURE_PATHS)} fixture file(s) under {RAW_DOCS_DIR}")


if __name__ == "__main__":
    generate()
