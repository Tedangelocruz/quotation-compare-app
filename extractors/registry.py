"""
Supplier Registry — Automatic supplier detection and extractor routing.

This is the brain of the enterprise extraction architecture.
It maintains a registry of all known extractors and automatically
selects the best one for each uploaded file.

Usage:
    from extractors.registry import detect_and_extract
    
    result = detect_and_extract(file, filename, api_key="...")
    print(result.badge_text)   # "🟢 IMCA (Deterministic) — 95% confidence"
    print(result.items)        # [{...}, {...}, ...]
"""

from typing import BinaryIO
from .base import BaseExtractor, ExtractionResult, ExtractorType, VerificationResult, VerificationStatus
from .imca import IMCAExtractor
from .supplier_b import SupplierBExtractor
from .llm_fallback import LLMFallbackExtractor


# Detection threshold — extractor must score above this to be auto-selected
CONFIDENCE_THRESHOLD = 0.7


class SupplierRegistry:
    """
    Registry of all available supplier extractors.
    
    Extractors are tried in registration order. The one with the
    highest confidence score above the threshold wins.
    
    To add a new supplier:
      1. Create a new extractor class in extractors/
      2. Register it here in __init__
    """
    
    def __init__(self):
        # Register extractors in priority order
        # Deterministic extractors first, AI fallback last
        self._extractors: list[type[BaseExtractor]] = [
            IMCAExtractor,
            SupplierBExtractor,
            # Add more suppliers here as needed:
            # SupplierCExtractor,
            # SupplierDExtractor,
        ]
        self._fallback = LLMFallbackExtractor
    
    def register(self, extractor_cls: type[BaseExtractor]):
        """Register a new extractor (inserted before the fallback)."""
        self._extractors.append(extractor_cls)
    
    def detect(self, file: BinaryIO, filename: str) -> list[dict]:
        """
        Run detection across all registered extractors.
        
        Returns list of {extractor_class, confidence, name, type}
        sorted by confidence descending.
        """
        results = []
        
        for ext_cls in self._extractors:
            try:
                file.seek(0)
                confidence = ext_cls.detect(file, filename)
                if confidence > 0:
                    results.append({
                        'extractor_class': ext_cls,
                        'confidence': confidence,
                        'name': ext_cls.name,
                        'type': ext_cls.extractor_type,
                    })
            except Exception:
                continue
        
        # Always include fallback
        try:
            file.seek(0)
            fb_confidence = self._fallback.detect(file, filename)
            if fb_confidence > 0:
                results.append({
                    'extractor_class': self._fallback,
                    'confidence': fb_confidence,
                    'name': self._fallback.name,
                    'type': self._fallback.extractor_type,
                })
        except Exception:
            pass
        
        file.seek(0)
        results.sort(key=lambda x: x['confidence'], reverse=True)
        return results
    
    def get_best_extractor(self, file: BinaryIO, filename: str) -> dict | None:
        """
        Get the best extractor for a file.
        
        Returns the highest-confidence detection result, 
        or the fallback if no deterministic extractor matches.
        """
        detections = self.detect(file, filename)
        
        if not detections:
            return None
        
        best = detections[0]
        
        # If best is below threshold and not the fallback, prefer fallback
        if best['confidence'] < CONFIDENCE_THRESHOLD and best['type'] != ExtractorType.AI:
            # Find the fallback in results
            for d in detections:
                if d['type'] == ExtractorType.AI:
                    return d
        
        return best
    
    def list_extractors(self) -> list[str]:
        """List all registered extractor names (for UI dropdown)."""
        names = [cls.name for cls in self._extractors]
        names.append(self._fallback.name)
        return names
    
    def get_extractor_by_name(self, name: str) -> type[BaseExtractor] | None:
        """Get an extractor class by its name (for manual override)."""
        for cls in self._extractors:
            if cls.name == name:
                return cls
        if self._fallback.name == name:
            return self._fallback
        return None


# Module-level singleton
_registry = SupplierRegistry()


def detect_and_extract(
    file: BinaryIO, 
    filename: str, 
    override_extractor: str = None,
    **kwargs
) -> ExtractionResult:
    """
    Main entry point: detect the supplier, extract items, and verify subtotals.
    
    Args:
        file: File-like object
        filename: Original filename
        override_extractor: If set, skip detection and use this extractor name
        **kwargs: Passed to the extractor's extract() method
                  (e.g. api_key, example_hints)
    
    Returns:
        ExtractionResult with items, supplier info, confidence, and verification
    """
    result = ExtractionResult()
    
    # Manual override
    if override_extractor:
        ext_cls = _registry.get_extractor_by_name(override_extractor)
        if ext_cls:
            extractor = ext_cls()
            file.seek(0)
            result.items = extractor.extract(file, filename, **kwargs)
            result.supplier_name = ext_cls.name
            result.extractor_name = ext_cls.name
            result.extractor_type = ext_cls.extractor_type
            result.confidence = 1.0  # Manual selection = full confidence
            result.verification = extractor.verify_subtotals(result.items)
            return result
    
    # Auto-detect
    detection = _registry.get_best_extractor(file, filename)
    
    if not detection:
        result.warnings.append("No extractor could handle this file.")
        return result
    
    ext_cls = detection['extractor_class']
    extractor = ext_cls()
    
    file.seek(0)
    result.items = extractor.extract(file, filename, **kwargs)
    result.supplier_name = detection['name']
    result.extractor_name = detection['name']
    result.extractor_type = detection['type']
    result.confidence = detection['confidence']
    
    # Run verification
    if result.items:
        result.verification = extractor.verify_subtotals(result.items)
    
    # Detect currency from items (if available)
    currencies = [i.get('currency') for i in result.items if i.get('currency')]
    if currencies:
        result.currency = currencies[0]
    
    return result


def detect_supplier(file: BinaryIO, filename: str) -> list[dict]:
    """
    Run detection only (without extraction).
    Useful for showing the detection badge before the user confirms.
    """
    return _registry.detect(file, filename)


def list_available_extractors() -> list[str]:
    """List all registered extractor names."""
    return _registry.list_extractors()
