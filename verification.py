"""
Subtotal Verification Module

Provides standalone verification functions that can work
independently of extractors. Useful for post-extraction validation
and for the UI to show verification status.
"""

from dataclasses import dataclass, field


@dataclass
class RowMismatch:
    """Details of a single row verification mismatch."""
    row_index: int
    sku: str
    expected: float
    actual: float
    diff: float
    reason: str = ""


@dataclass 
class VerifyResult:
    """Standalone verification result (independent of extractors)."""
    passed: bool = True
    total_rows: int = 0
    matched_rows: int = 0
    mismatched_rows: int = 0
    mismatches: list[RowMismatch] = field(default_factory=list)
    document_total_computed: float | None = None
    document_total_stated: float | None = None
    status_emoji: str = "⏳"
    status_text: str = "Pending"
    
    def set_status(self):
        """Compute status based on results."""
        if self.mismatched_rows == 0:
            self.passed = True
            self.status_emoji = "✅"
            self.status_text = f"Verified ({self.matched_rows}/{self.total_rows} items match)"
        elif self.mismatched_rows <= max(1, self.total_rows * 0.05):
            self.passed = True  # Still acceptable
            self.status_emoji = "⚠️"
            self.status_text = f"{self.mismatched_rows} minor mismatches — review recommended"
        else:
            self.passed = False
            self.status_emoji = "❌"
            self.status_text = f"{self.mismatched_rows} mismatches found — manual review required"


def verify_items(items: list[dict], tolerance: float = 0.02) -> VerifyResult:
    """
    Verify extracted items for consistency.
    
    Checks:
      1. Row-level: quantity × unit_price ≈ total_price
      2. Document-level: sum of totals is consistent
    
    Args:
        items: List of item dicts with quantity, unit_price, total_price
        tolerance: Acceptable relative error (default 2%)
    
    Returns:
        VerifyResult with details of any mismatches
    """
    result = VerifyResult(total_rows=len(items))
    
    for idx, item in enumerate(items):
        qty = float(item.get('quantity') or 0)
        price = float(item.get('unit_price') or 0)
        total = float(item.get('total_price') or 0)
        sku = item.get('product_id') or item.get('sku') or f'row_{idx}'
        
        expected = round(qty * price, 2)
        actual = round(total, 2)
        
        # Both zero — trivially correct
        if actual == 0 and expected == 0:
            result.matched_rows += 1
            continue
        
        # Can't compute expected
        if expected == 0 and actual != 0:
            result.mismatched_rows += 1
            result.mismatches.append(RowMismatch(
                row_index=idx, sku=sku,
                expected=0, actual=actual, diff=actual,
                reason="Cannot verify (qty=0 or price=0 but total is nonzero)"
            ))
            continue
        
        # Relative error check
        rel_error = abs(actual - expected) / max(abs(expected), 1e-9)
        
        if rel_error <= tolerance:
            result.matched_rows += 1
        else:
            result.mismatched_rows += 1
            result.mismatches.append(RowMismatch(
                row_index=idx, sku=sku,
                expected=expected, actual=actual,
                diff=round(actual - expected, 2)
            ))
    
    # Document-level totals
    result.document_total_computed = round(
        sum(round(float(i.get('quantity') or 0) * float(i.get('unit_price') or 0), 2) for i in items), 2
    )
    result.document_total_stated = round(
        sum(float(i.get('total_price') or 0) for i in items), 2
    )
    
    result.set_status()
    return result


def verify_document_total(items: list[dict], stated_total: float, tolerance: float = 0.02) -> bool:
    """
    Quick check: does the sum of line totals match a stated document total?
    
    Args:
        items: Extracted items
        stated_total: The total stated on the document
        tolerance: Acceptable relative error
    
    Returns:
        True if they match within tolerance
    """
    computed = sum(float(i.get('total_price') or 0) for i in items)
    
    if stated_total == 0 and computed == 0:
        return True
    if stated_total == 0:
        return False
    
    rel_error = abs(computed - stated_total) / abs(stated_total)
    return rel_error <= tolerance
