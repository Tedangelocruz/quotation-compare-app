"""
Supplier B Template Extractor (PDF-based)

This is a TEMPLATE for adding a second known supplier.
To customize for your supplier:

1. Update the `name` class attribute
2. Update `SUPPLIER_KEYWORDS` with terms found in their PDFs
3. Implement the column/field detection logic in `extract()`
4. Test with a real PDF from the supplier

How to identify a new supplier's PDF structure:
  - Open a sample PDF and note the header/footer text
  - Identify column layout (SKU position, price position, etc.)
  - Check for unique formatting (specific date formats, logos mentioned, etc.)
"""

from typing import BinaryIO
from pypdf import PdfReader
from io import BytesIO

from .base import BaseExtractor, ExtractorType
from utils import parse_number, is_valid_sku


# Keywords that identify this supplier in PDF text
# Update these with actual keywords from Supplier B's PDFs
SUPPLIER_KEYWORDS = [
    # Example keywords — REPLACE with real ones:
    # "ACME INDUSTRIAL",
    # "RNC: 123456789",
    # "COTIZACIÓN",
]

# Supplier name as it appears in their documents
SUPPLIER_DISPLAY_NAME = "Supplier B"


class SupplierBExtractor(BaseExtractor):
    """
    Template extractor for a known PDF-based supplier.
    
    USAGE:
      1. Get a sample PDF from the supplier
      2. Run: python -c "from pypdf import PdfReader; r = PdfReader('sample.pdf'); print(r.pages[0].extract_text()[:2000])"
      3. Identify keywords and column positions
      4. Update this file accordingly
    """
    
    name = SUPPLIER_DISPLAY_NAME
    supported_extensions = ['.pdf']
    extractor_type = ExtractorType.DETERMINISTIC
    
    @classmethod
    def detect(cls, file: BinaryIO, filename: str) -> float:
        """
        Detect Supplier B PDFs by checking for known keywords in the text.
        """
        fname_lower = filename.lower()
        
        # Must be PDF
        if not fname_lower.endswith('.pdf'):
            return 0.0
        
        # If no keywords are configured, this extractor is disabled
        if not SUPPLIER_KEYWORDS:
            return 0.0
        
        try:
            file.seek(0)
            reader = PdfReader(file)
            file.seek(0)
            
            # Read first 2 pages for detection
            text = ""
            for page in reader.pages[:2]:
                text += (page.extract_text() or "")
            
            text_upper = text.upper()
            
            # Count keyword matches
            matches = sum(1 for kw in SUPPLIER_KEYWORDS if kw.upper() in text_upper)
            
            if matches >= 2:
                return 0.9
            elif matches == 1:
                return 0.7
            
            # Filename check
            supplier_name_clean = SUPPLIER_DISPLAY_NAME.lower().replace(' ', '')
            if supplier_name_clean in fname_lower:
                return 0.6
            
            return 0.0
        except Exception:
            return 0.0
    
    def extract(self, file: BinaryIO, filename: str, **kwargs) -> list[dict]:
        """
        Extract items from Supplier B PDF.
        
        TODO: Implement actual parsing logic based on the supplier's PDF format.
        
        Typical steps:
          1. Read all pages
          2. Split text into lines
          3. Identify header row to determine column positions
          4. Parse each data line into fields
          5. Map fields to standard item dict
        """
        file.seek(0)
        reader = PdfReader(file)
        
        full_text = ""
        for page in reader.pages:
            full_text += (page.extract_text() or "") + "\n"
        
        items = []
        lines = full_text.split('\n')
        
        # --- TEMPLATE LOGIC ---
        # Replace this with supplier-specific parsing.
        # Below is a generic heuristic similar to the original app.py logic.
        
        header_keywords = [
            "FACTURA", "RNC:", "CLIENTE:", "VENDEDOR:", 
            "FECHA:", "TEL:", "PÁGINA", "CANT.", "PRECIO", "DESCRIPCIÓN"
        ]
        
        for line in lines:
            line = line.strip()
            if not line or len(line) < 10:
                continue
            if any(kw in line.upper() for kw in header_keywords):
                continue
            
            parts = line.split()
            if len(parts) < 3:
                continue
            
            # Right-to-left number scanning
            trailing_numbers = []
            text_parts = []
            found_text = False
            
            for i in range(len(parts) - 1, -1, -1):
                val = parse_number(parts[i])
                if val is not None and 0 < val < 1_000_000 and not found_text:
                    trailing_numbers.insert(0, val)
                else:
                    found_text = True
                    text_parts.insert(0, parts[i])
            
            if len(trailing_numbers) < 1:
                continue
            
            # Find SKU in text parts
            product_id = None
            desc_parts = []
            
            for part in text_parts:
                clean = part.strip(':,.;()')
                if product_id is None and is_valid_sku(clean):
                    product_id = clean
                else:
                    desc_parts.append(part)
            
            if not product_id:
                continue
            
            description = " ".join(desc_parts[:20]) if desc_parts else " ".join(text_parts[:20])
            
            qty = 1.0
            unit_price = 0.0
            total_price = 0.0
            
            if len(trailing_numbers) >= 3:
                qty = trailing_numbers[0]
                unit_price = trailing_numbers[1]
                total_price = trailing_numbers[-1]
            elif len(trailing_numbers) == 2:
                unit_price = trailing_numbers[0]
                total_price = trailing_numbers[1]
            elif len(trailing_numbers) == 1:
                total_price = trailing_numbers[0]
                unit_price = total_price
            
            items.append({
                "supplier_name": SUPPLIER_DISPLAY_NAME,
                "product_name": description,
                "product_id": product_id,
                "quantity": qty,
                "unit_price": unit_price,
                "total_price": total_price,
            })
        
        return items
