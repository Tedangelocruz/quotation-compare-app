"""
Abstract base class for all supplier extractors.

Every extractor (deterministic or AI) must implement this interface.
This ensures consistent behavior across the extraction pipeline.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import BinaryIO


class VerificationStatus(Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    PENDING = "pending"


class ExtractorType(Enum):
    DETERMINISTIC = "deterministic"
    AI = "ai"
    MANUAL = "manual"


@dataclass
class VerificationResult:
    """Result of subtotal verification for a set of extracted items."""
    status: VerificationStatus = VerificationStatus.PENDING
    total_items: int = 0
    matched_items: int = 0
    mismatched_items: int = 0
    mismatches: list = field(default_factory=list)  # list of {sku, expected, actual, diff}
    document_total_expected: float | None = None
    document_total_actual: float | None = None
    
    @property
    def summary(self) -> str:
        if self.status == VerificationStatus.PASS:
            return f"✅ Subtotals verified ({self.matched_items}/{self.total_items} items match)"
        elif self.status == VerificationStatus.WARN:
            return f"⚠️ {self.mismatched_items} mismatches found — review recommended"
        elif self.status == VerificationStatus.FAIL:
            return f"❌ Verification failed: {self.mismatched_items} critical mismatches"
        return "⏳ Verification pending"


@dataclass
class ExtractionResult:
    """Complete result from the extraction pipeline."""
    items: list[dict] = field(default_factory=list)
    supplier_name: str = "Unknown"
    extractor_name: str = "manual"
    extractor_type: ExtractorType = ExtractorType.MANUAL
    confidence: float = 0.0
    verification: VerificationResult = field(default_factory=VerificationResult)
    currency: str = "DOP"
    warnings: list[str] = field(default_factory=list)
    
    @property
    def badge_color(self) -> str:
        """Color for UI badge based on extractor type."""
        if self.extractor_type == ExtractorType.DETERMINISTIC:
            return "🟢"
        elif self.extractor_type == ExtractorType.AI:
            return "🟡"
        return "🔵"
    
    @property
    def badge_text(self) -> str:
        """Full badge text for UI display."""
        pct = int(self.confidence * 100)
        return f"{self.badge_color} {self.supplier_name} ({self.extractor_type.value.title()}) — {pct}% confidence"


class BaseExtractor(ABC):
    """
    Abstract base class for all supplier extractors.
    
    Subclasses must implement:
      - name: human-readable supplier name
      - supported_extensions: list of file extensions (e.g. ['.pdf', '.xlsx'])
      - detect(): determines if this extractor handles the file
      - extract(): parses the file into standardized items
    """
    
    name: str = "Base"
    supported_extensions: list[str] = []
    extractor_type: ExtractorType = ExtractorType.DETERMINISTIC
    
    @classmethod
    @abstractmethod
    def detect(cls, file: BinaryIO, filename: str) -> float:
        """
        Determine if this extractor can handle the given file.
        
        Args:
            file: File-like object (seeked to start)
            filename: Original filename
            
        Returns:
            Confidence score 0.0 to 1.0. Higher = more confident.
            Scores above 0.7 are considered a match.
        """
        pass
    
    @abstractmethod
    def extract(self, file: BinaryIO, filename: str, **kwargs) -> list[dict]:
        """
        Extract line items from the file.
        
        Args:
            file: File-like object (seeked to start)
            filename: Original filename
            **kwargs: Extractor-specific options (e.g. api_key, hints)
            
        Returns:
            List of dicts, each with keys:
              supplier_name, product_name, product_id/sku, 
              quantity, unit_price, total_price, equipment (optional)
        """
        pass
    
    def verify_subtotals(self, items: list[dict], tolerance: float = 0.02) -> VerificationResult:
        """
        Verify that quantity × unit_price ≈ total_price for each item.
        Default implementation — can be overridden by subclasses for 
        document-level verification.
        
        Args:
            items: Extracted items
            tolerance: Acceptable relative error (default 2%)
        """
        result = VerificationResult(total_items=len(items))
        
        for item in items:
            qty = item.get('quantity') or 0
            price = item.get('unit_price') or 0
            total = item.get('total_price') or 0
            expected = qty * price
            
            if total == 0 and expected == 0:
                result.matched_items += 1
                continue
            
            if expected == 0:
                # Can't verify, treat as warning
                result.mismatched_items += 1
                result.mismatches.append({
                    'sku': item.get('product_id') or item.get('sku', '?'),
                    'expected': expected,
                    'actual': total,
                    'diff': total,
                    'reason': 'Cannot compute expected (qty or price is 0)'
                })
                continue
                
            rel_error = abs(total - expected) / max(abs(expected), 1e-9)
            
            if rel_error <= tolerance:
                result.matched_items += 1
            else:
                result.mismatched_items += 1
                result.mismatches.append({
                    'sku': item.get('product_id') or item.get('sku', '?'),
                    'expected': round(expected, 2),
                    'actual': round(total, 2),
                    'diff': round(total - expected, 2)
                })
        
        # Set status
        if result.mismatched_items == 0:
            result.status = VerificationStatus.PASS
        elif result.mismatched_items <= max(1, result.total_items * 0.05):
            result.status = VerificationStatus.WARN
        else:
            result.status = VerificationStatus.FAIL
        
        return result
