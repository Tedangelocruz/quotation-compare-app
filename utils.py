"""
Utility functions for Cotización Rapida.
Extracted from monolithic app.py for modular architecture.
"""

import base64
import os
import re
import pandas as pd


def get_base64_image(image_path: str) -> str:
    """Convert image to base64 string for HTML embedding."""
    try:
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode()
    except Exception:
        return ""


def parse_number(num_str) -> float | None:
    """
    Parse a number string that may contain currency symbols, 
    thousands separators, and different decimal formats.
    
    Handles:
    - US format: 1,234.56
    - European format: 1.234,56
    - Currency symbols: $, €, S/, USD, EUR
    """
    if not isinstance(num_str, str):
        return num_str
    
    clean_str = (
        num_str.upper()
        .replace('$', '').replace('€', '').replace('S/', '')
        .replace('USD', '').replace('EUR', '').replace('DOP', '').replace('RD', '')
        .strip()
        .replace(' ', '')
    )
    
    if not clean_str:
        return None

    try:
        if ',' in clean_str and '.' in clean_str:
            last_comma = clean_str.rfind(',')
            last_dot = clean_str.rfind('.')
            if last_comma > last_dot:  # 1.234,56 (European)
                clean_str = clean_str.replace('.', '').replace(',', '.')
            else:  # 1,234.56 (US)
                clean_str = clean_str.replace(',', '')
        elif ',' in clean_str:
            parts = clean_str.split(',')
            if len(parts[-1]) == 2:
                clean_str = clean_str.replace(',', '.')
            elif len(parts[-1]) == 3:
                clean_str = clean_str.replace(',', '')
            else:
                clean_str = clean_str.replace(',', '.')
        return float(clean_str)
    except ValueError:
        return None


def normalize_sku(sku) -> str:
    """
    Normalize SKU for comparison by removing hyphens, spaces, 
    underscores and converting to uppercase.
    """
    if not sku or pd.isna(sku):
        return ''
    return str(sku).upper().replace('-', '').replace(' ', '').replace('_', '')


def is_valid_sku(sku: str, min_length: int = 6) -> bool:
    """Check if a string is a valid SKU (at least min_length chars, alphanumeric)."""
    if not sku or len(sku.strip()) < min_length:
        return False
    clean = sku.strip()
    return (
        (any(c.isdigit() for c in clean) and any(c.isalpha() for c in clean)) or
        clean.isdigit() or
        (clean.isupper() and clean.isalnum())
    )
