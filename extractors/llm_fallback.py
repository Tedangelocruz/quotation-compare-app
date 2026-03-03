"""
LLM Fallback Extractor

Uses Google Gemini to extract items from unknown/unrecognized documents.
This is the safety net — when no deterministic extractor matches,
the AI takes its best shot.

Wraps the original extract_with_llm() logic in the BaseExtractor interface.
"""

from typing import BinaryIO
import json
from pypdf import PdfReader

from .base import BaseExtractor, ExtractorType

try:
    import google.generativeai as genai
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False


class LLMFallbackExtractor(BaseExtractor):
    """
    AI-powered fallback extractor using Google Gemini.
    
    Always returns low confidence from detect() so deterministic 
    extractors take priority. Requires an API key to function.
    """
    
    name = "AI (Gemini)"
    supported_extensions = ['.pdf']  # Can only parse PDFs via text extraction
    extractor_type = ExtractorType.AI
    
    @classmethod
    def detect(cls, file: BinaryIO, filename: str) -> float:
        """
        Always returns 0.1 — this extractor is the universal fallback.
        It should only be used when no deterministic extractor matches.
        """
        # Only handle PDFs
        if not filename.lower().endswith('.pdf'):
            return 0.0
        return 0.1  # Always available but lowest priority
    
    def extract(self, file: BinaryIO, filename: str, **kwargs) -> list[dict]:
        """
        Extract items using Google Gemini LLM.
        
        kwargs:
            api_key (str): Gemini API key (required)
            example_hints (dict): Optional hints for column mapping
                {'sku': '...', 'item': '...', 'qty': '...', 'price': '...'}
        """
        api_key = kwargs.get('api_key')
        example_hints = kwargs.get('example_hints', {})
        
        if not api_key:
            return []
        
        if not HAS_GENAI:
            return []
        
        # Extract text from PDF
        file.seek(0)
        reader = PdfReader(file)
        
        full_text = ""
        for page in reader.pages:
            full_text += (page.extract_text() or "") + "\n"
        
        if not full_text.strip():
            return []
        
        # Try page-by-page first, then full text
        items = self._extract_with_llm(full_text, api_key, example_hints)
        
        if not items:
            # Try individual pages
            for page in reader.pages:
                text = page.extract_text() or ""
                page_items = self._extract_with_llm(text, api_key, example_hints)
                if page_items:
                    items.extend(page_items)
                    break  # Found items on a page
        
        return items
    
    def _extract_with_llm(self, text: str, api_key: str, example_hints: dict = None) -> list[dict]:
        """Core LLM extraction logic."""
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-2.5-flash')
            
            hint_text = ""
            if example_hints and isinstance(example_hints, dict):
                hints = []
                if example_hints.get('sku'):
                    hints.append(f"Look for the value '{example_hints['sku']}' to identify the 'product_id' column.")
                if example_hints.get('item'):
                    hints.append(f"Look for the value '{example_hints['item']}' to identify the 'product_name' column.")
                if example_hints.get('qty'):
                    hints.append(f"Look for the value '{example_hints['qty']}' to identify the 'quantity' column.")
                if example_hints.get('price'):
                    hints.append(f"Look for the value '{example_hints['price']}' to identify the 'unit_price' column.")
                
                if hints:
                    hint_text = "IMPORTANT MAPPING HINTS (Use these values to locate columns):\n" + "\n".join(hints)
            
            prompt = f"""
            You are a parser that converts PDF price quotations into structured line items.
            You MUST always return a JSON object with a top-level items array.
            Each element in items is an object with:
            supplier_name (string), product_name (string), product_id (string or null),
            quantity (number or null), unit_price (number or null), tax_amount (number or null),
            transport_cost (number or null), total_price (number or null)

            Rules:
            If the PDF is messy or unclear, make your best reasonable guess.
            If some value is missing or not numeric, use null instead of skipping the whole item.
            Never return an empty items array. If you can only find one rough line item, return that.
            Do not include explanations or comments – output ONLY valid JSON.
            {hint_text}
            
            Here is the text content of the PDF:
            """ + text

            response = model.generate_content(prompt)
            content = response.text
            
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            data = json.loads(content)
            raw_items = data if isinstance(data, list) else data.get('items', [])
            
            # Filter items: SKU must be at least 6 characters
            valid_items = []
            for item in raw_items:
                pid = item.get('product_id')
                if pid and isinstance(pid, str) and len(pid.strip()) >= 6:
                    valid_items.append(item)
            
            return valid_items
        except Exception:
            return []
