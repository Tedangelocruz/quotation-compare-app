"""
Extractors module — Enterprise supplier extraction framework.

Architecture:
  Known suppliers → Deterministic extractors (IMCA, Supplier B, etc.)
  Unknown suppliers → AI fallback (Gemini LLM)
  Manual override → User safety net
"""

from .registry import SupplierRegistry, detect_and_extract
from .base import BaseExtractor, ExtractionResult, VerificationResult
from .imca import IMCAExtractor
from .supplier_b import SupplierBExtractor
from .llm_fallback import LLMFallbackExtractor

__all__ = [
    'SupplierRegistry', 'detect_and_extract',
    'BaseExtractor', 'ExtractionResult', 'VerificationResult',
    'IMCAExtractor', 'SupplierBExtractor', 'LLMFallbackExtractor',
]
