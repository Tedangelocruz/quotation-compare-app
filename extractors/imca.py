"""
IMCA Deterministic Extractor

Handles IMCA quotation files in Excel (.xlsx) format.
Based on real file analysis of Cotizacion_01Q202560_imca.xlsx.

Expected columns:
  A: Item (row number)
  B: Proveedor (supplier — always "IMCA")
  C: Número de Cotización (quote number)
  D: Fecha Cotización (quote date)
  E: Equipo (equipment reference, e.g. "REC21 | 416C | 4ZN00979")
  F: Número de Parte (part number → SKU)
  G: Descripción (description → product_name)
  H: Cantidad (quantity)
  I: Precio Unitario DOP (unit price in DOP)
  J: Total DOP (line total)
  K: Moneda (currency — usually "DOP")
"""

from typing import BinaryIO
import pandas as pd
from io import BytesIO

from .base import BaseExtractor, ExtractorType, VerificationResult, VerificationStatus


# Column mapping from IMCA Excel headers to internal field names
IMCA_COLUMN_MAP = {
    'Número de Parte': 'product_id',
    'Descripción': 'product_name',
    'Cantidad': 'quantity',
    'Precio Unitario DOP': 'unit_price',
    'Total DOP': 'total_price',
    'Equipo': 'equipment',
    'Proveedor': 'supplier_name',
    'Moneda': 'currency',
    'Número de Cotización': 'quote_number',
    'Fecha Cotización': 'quote_date',
}

# Alternate header names (for robustness)
IMCA_HEADER_ALIASES = {
    'Numero de Parte': 'Número de Parte',
    'Part Number': 'Número de Parte',
    'Descripcion': 'Descripción',
    'Description': 'Descripción',
    'Qty': 'Cantidad',
    'Unit Price': 'Precio Unitario DOP',
    'Total': 'Total DOP',
    'Currency': 'Moneda',
}


class IMCAExtractor(BaseExtractor):
    """
    Deterministic extractor for IMCA supplier quotations.
    
    IMCA sends quotations as Excel files with a consistent column layout.
    This extractor reads the spreadsheet directly — no AI needed.
    """
    
    name = "IMCA"
    supported_extensions = ['.xlsx', '.xls']
    extractor_type = ExtractorType.DETERMINISTIC
    
    @classmethod
    def detect(cls, file: BinaryIO, filename: str) -> float:
        """
        Detect IMCA files by:
          1. Filename contains "imca" (0.8 confidence)
          2. Excel with "Proveedor" column containing "IMCA" (0.95 confidence)
          3. File extension is .xlsx/.xls (required, 0.0 if not)
        """
        fname_lower = filename.lower()
        
        # Must be Excel format
        if not any(fname_lower.endswith(ext) for ext in cls.supported_extensions):
            return 0.0
        
        # Filename check (fast path)
        if 'imca' in fname_lower:
            try:
                # Verify it's actually a valid IMCA Excel
                file.seek(0)
                df = pd.read_excel(file, nrows=3)
                file.seek(0)
                
                # Check for expected columns
                cols = [c.strip() for c in df.columns]
                has_parte = any('Parte' in c for c in cols)
                has_precio = any('Precio' in c for c in cols)
                
                if has_parte and has_precio:
                    return 0.95
                return 0.8
            except Exception:
                return 0.5  # Filename matches but can't read — still likely IMCA
        
        # Content check (slower path for generic .xlsx files)
        try:
            file.seek(0)
            df = pd.read_excel(file, nrows=5)
            file.seek(0)
            
            cols = [str(c).strip() for c in df.columns]
            
            # Check for IMCA-specific column structure
            has_proveedor = any('Proveedor' in c for c in cols)
            has_parte = any('Parte' in c for c in cols)
            
            if has_proveedor and has_parte:
                # Check if "IMCA" appears in first few rows
                for col in df.columns:
                    if 'Proveedor' in str(col):
                        if df[col].astype(str).str.contains('IMCA', case=False).any():
                            return 0.95
                return 0.6  # Has structure but no IMCA mention
            
            return 0.0
        except Exception:
            return 0.0
    
    def extract(self, file: BinaryIO, filename: str, **kwargs) -> list[dict]:
        """
        Extract items from IMCA Excel file.
        
        Returns list of standardized item dicts.
        """
        file.seek(0)
        df = pd.read_excel(file)
        
        # Normalize column names (handle aliases)
        normalized_cols = {}
        for col in df.columns:
            col_stripped = str(col).strip()
            if col_stripped in IMCA_HEADER_ALIASES:
                normalized_cols[col] = IMCA_HEADER_ALIASES[col_stripped]
            else:
                normalized_cols[col] = col_stripped
        df.rename(columns=normalized_cols, inplace=True)
        
        items = []
        
        for _, row in df.iterrows():
            # Map columns using our mapping
            item = {}
            
            for excel_col, internal_key in IMCA_COLUMN_MAP.items():
                if excel_col in df.columns:
                    val = row.get(excel_col)
                    if pd.notna(val):
                        item[internal_key] = val
                    else:
                        item[internal_key] = None
            
            # Skip rows without a part number (these are usually totals or headers)
            if not item.get('product_id'):
                continue
            
            # Ensure product_id is a string
            item['product_id'] = str(item['product_id']).strip()
            
            # SKU validation (must be >= 6 chars for consistency)
            if len(item['product_id']) < 6:
                continue
            
            # Set supplier name
            item['supplier_name'] = item.get('supplier_name', 'IMCA') or 'IMCA'
            
            # Ensure numeric fields
            item['quantity'] = float(item.get('quantity') or 0)
            item['unit_price'] = float(item.get('unit_price') or 0)
            item['total_price'] = float(item.get('total_price') or 0)
            
            # Equipment info (IMCA-specific metadata)
            item['equipment'] = str(item.get('equipment', '')) if item.get('equipment') else ''
            
            items.append(item)
        
        return items
    
    def verify_subtotals(self, items: list[dict], tolerance: float = 0.02) -> VerificationResult:
        """
        IMCA-specific verification:
          1. Row-level: quantity × unit_price ≈ total_price
          2. Rounds to 2 decimal places (IMCA uses DOP precision)
        """
        result = VerificationResult(total_items=len(items))
        
        for item in items:
            qty = item.get('quantity', 0)
            price = item.get('unit_price', 0)
            total = item.get('total_price', 0)
            expected = round(qty * price, 2)
            actual = round(total, 2)
            
            if actual == 0 and expected == 0:
                result.matched_items += 1
                continue
            
            if expected == 0:
                result.mismatched_items += 1
                result.mismatches.append({
                    'sku': item.get('product_id', '?'),
                    'expected': expected,
                    'actual': actual,
                    'diff': actual,
                    'reason': 'Cannot compute (qty or price is 0)'
                })
                continue
            
            rel_error = abs(actual - expected) / max(abs(expected), 1e-9)
            
            if rel_error <= tolerance:
                result.matched_items += 1
            else:
                result.mismatched_items += 1
                result.mismatches.append({
                    'sku': item.get('product_id', '?'),
                    'expected': expected,
                    'actual': actual,
                    'diff': round(actual - expected, 2)
                })
        
        # Document-level: sum of totals
        doc_total = sum(i.get('total_price', 0) for i in items)
        computed_total = sum(round(i.get('quantity', 0) * i.get('unit_price', 0), 2) for i in items)
        result.document_total_expected = round(computed_total, 2)
        result.document_total_actual = round(doc_total, 2)
        
        # Status
        if result.mismatched_items == 0:
            result.status = VerificationStatus.PASS
        elif result.mismatched_items <= max(1, result.total_items * 0.05):
            result.status = VerificationStatus.WARN
        else:
            result.status = VerificationStatus.FAIL
        
        return result
