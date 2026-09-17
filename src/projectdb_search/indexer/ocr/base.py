"""OCR backend interface.

Not implemented in this phase (no PDF text extraction or scanned-image
handling yet — see the project plan's Phase 4). Defined now so the pipeline's
extension point exists: a future `TesseractBackend` only needs to subclass
`OCRBackend`, with no changes to `indexer/pipeline.py`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class OCRResult:
    text: str
    confidence: float  # 0.0-1.0


class OCRBackend(ABC):
    @abstractmethod
    def recognize(self, images: list) -> OCRResult: ...
