"""
Extraction Pipeline — Orchestration layer.

Ties together supplier detection, extraction, verification,
and provides a clean API for the Streamlit UI.

Usage:
    from pipeline import process_file
    
    result = process_file(uploaded_file, api_key="...")
    if result.items:
        print(result.badge_text)
        print(result.verification.summary)
"""

from typing import BinaryIO
from io import BytesIO

from extractors.registry import detect_and_extract, detect_supplier, list_available_extractors
from extractors.base import ExtractionResult, ExtractorType
from verification import verify_items


def process_file(
    file: BinaryIO,
    filename: str,
    api_key: str = None,
    example_hints: dict = None,
    override_extractor: str = None,
) -> ExtractionResult:
    """
    Full pipeline: detect → extract → verify.
    
    This is the main function called by the Streamlit UI for each file.
    
    Args:
        file: Uploaded file object
        filename: Original filename
        api_key: Gemini API key (for AI fallback)
        example_hints: User-provided mapping hints
        override_extractor: Force a specific extractor
    
    Returns:
        ExtractionResult with items, detection info, and verification status
    """
    # Build kwargs for the extractor
    kwargs = {}
    if api_key:
        kwargs['api_key'] = api_key
    if example_hints:
        kwargs['example_hints'] = example_hints
    
    # Run the pipeline
    file.seek(0)
    result = detect_and_extract(
        file, filename,
        override_extractor=override_extractor,
        **kwargs
    )
    
    # Run standalone verification (augments the extractor's own verification)
    if result.items:
        standalone_verify = verify_items(result.items)
        
        # If the extractor didn't do its own verification, use ours
        if result.verification.status.value == 'pending':
            result.verification.status = (
                result.verification.status.__class__(standalone_verify.status_emoji == "✅" and "pass" or 
                                                       standalone_verify.status_emoji == "⚠️" and "warn" or "fail")
            )
            result.verification.total_items = standalone_verify.total_rows
            result.verification.matched_items = standalone_verify.matched_rows
            result.verification.mismatched_items = standalone_verify.mismatched_rows
    
    return result


def preview_detection(file: BinaryIO, filename: str) -> list[dict]:
    """
    Preview which extractors would match a file (for UI badge display).
    
    Returns list of {name, confidence, type} sorted by confidence.
    """
    file.seek(0)
    detections = detect_supplier(file, filename)
    file.seek(0)
    
    return [
        {
            'name': d['name'],
            'confidence': d['confidence'],
            'type': d['type'].value,
            'badge': _format_badge(d),
        }
        for d in detections
    ]


def _format_badge(detection: dict) -> str:
    """Format a detection result as a UI badge string."""
    ext_type = detection['type']
    pct = int(detection['confidence'] * 100)
    
    if ext_type == ExtractorType.DETERMINISTIC:
        emoji = "🟢"
    elif ext_type == ExtractorType.AI:
        emoji = "🟡"
    else:
        emoji = "🔵"
    
    return f"{emoji} {detection['name']} ({ext_type.value.title()}) — {pct}% confidence"


def get_extractor_names() -> list[str]:
    """Get list of all available extractor names for UI dropdown."""
    return list_available_extractors()
