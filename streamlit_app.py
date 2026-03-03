import streamlit as st
import pandas as pd
import sqlite3
import json
import os
from pypdf import PdfReader
import google.generativeai as genai
from io import BytesIO
import re
import base64

# -----------------------------
# Helpers
# -----------------------------
def get_base64_image(image_path):
    """Convert image to base64 string for HTML embedding"""
    try:
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode()
    except Exception:
        return ""

def parse_number(num_str):
    """Parse localized currency/number strings to float."""
    if num_str is None:
        return None
    if isinstance(num_str, (int, float)):
        return float(num_str)

    if not isinstance(num_str, str):
        return None

    clean_str = (
        num_str.upper()
        .replace("$", "")
        .replace("€", "")
        .replace("S/", "")
        .replace("USD", "")
        .replace("EUR", "")
        .strip()
    )
    clean_str = clean_str.replace(" ", "")
    if not clean_str:
        return None

    try:
        if "," in clean_str and "." in clean_str:
            last_comma = clean_str.rfind(",")
            last_dot = clean_str.rfind(".")
            if last_comma > last_dot:  # 1.234,56
                clean_str = clean_str.replace(".", "").replace(",", ".")
            else:  # 1,234.56
                clean_str = clean_str.replace(",", "")
        elif "," in clean_str:
            parts = clean_str.split(",")
            if len(parts[-1]) == 2:
                clean_str = clean_str.replace(",", ".")
            elif len(parts[-1]) == 3:
                clean_str = clean_str.replace(",", "")
            else:
                clean_str = clean_str.replace(",", ".")
        return float(clean_str)
    except ValueError:
        return None

def read_pdf_text(uploaded_file):
    """Try extracting text from PDF pages. Returns combined text."""
    try:
        reader = PdfReader(uploaded_file)
        text = ""
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"
        return text
    except Exception:
        return ""

# -----------------------------
# Supplier dropdown options
# -----------------------------
SUPPLIER_OPTIONS = [
    "Auto (recommended)",
    "IMCA / Parts.Cat (Cart)",
    "Supplier B (Format B)",
    "Generic (Gemini / OCR)",
]

def detect_supplier_format(text: str) -> str:
    """Auto-detect supplier format based on extracted text."""
    t = (text or "").lower()

    # IMCA / Parts.Cat cart
    if "carro de compras" in t and "parts.cat.com" in t:
        return "IMCA / Parts.Cat (Cart)"

    # CES / Caterpillar Export Services quotation (your PCLT_56Q.... format)
    if "caterpillar export services" in t or "quote#:" in t or "* q u o t a t i o n *" in t:
        return "Supplier B (Format B)"

    return "Generic (Gemini / OCR)"

# -----------------------------
# IMCA / Parts.Cat deterministic extractor (text-based PDFs)
# -----------------------------
def extract_parts_cat_cart(text):
    """
    Extract items from Parts.Cat / IMCA cart PDF (text-based).
    Expected pattern per item:
      <itemNo> <qty> <SKU>: <description>
      ...
      DOP
      $<old> c/u
      $<new> c/u
    """
    items = []
    pattern = re.compile(
        r"\n?\d+\s+(\d+(?:\.\d+)?)\s+([A-Z0-9\-]+):\s+(.+?)(?=\nDOP)",
        re.DOTALL
    )
    matches = list(pattern.finditer(text or ""))

    for match in matches:
        qty = parse_number(match.group(1)) or 0
        sku = match.group(2).strip()
        description = " ".join(match.group(3).split()).strip()

        start_pos = match.end()
        next_slice = (text or "")[start_pos:start_pos + 450]

        prices = re.findall(r"\$([\d,]+\.\d{2})\s*c/u", next_slice)
        unit_price = None
        total_price = None

        if prices:
            unit_price = parse_number(prices[-1])  # last = discounted/current
            if unit_price is not None:
                total_price = unit_price * float(qty)

        items.append({
            "supplier_name": "IMCA / Parts.Cat",
            "product_name": description,
            "product_id": sku,
            "quantity": float(qty),
            "unit_price": float(unit_price) if unit_price is not None else None,
            "total_price": float(total_price) if total_price is not None else None
        })

    return items

# -----------------------------
# Supplier B (CES Quote) extractor for your PCLT_56Q... format
# -----------------------------
def extract_ces_quote(text):
    """
    Extract items from Caterpillar Export Services quotation layout:
    Columns typically:
      ItemNo  PartNumber  Description  Qty  Pounds  UnitPrice  ExtendedPrice
    Example line from your PDF:
      1 178-2345 SENSOR GP 1 .500 180.29 $ 180.29
    """
    items = []
    if not text:
        return items

    # Narrow to the table region if possible
    # We take everything after the header line containing "Item Part Qty" up to "TOTAL WEIGHT"
    start_idx = text.find("Item Part Qty")
    if start_idx != -1:
        work = text[start_idx:]
    else:
        work = text

    end_match = re.search(r"TOTAL\s+WEIGHT", work, re.IGNORECASE)
    if end_match:
        work = work[:end_match.start()]

    lines = [ln.strip() for ln in work.splitlines() if ln.strip()]

    # Regex for a row:
    # item_no  part_no  description...  qty  pounds  unit_price  $  extended
    row_re = re.compile(
        r"^(\d+)\s+([A-Z0-9\-]+)\s+(.+?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+([\d,]+\.\d{2})\s+\$\s*([\d,]+\.\d{2})$"
    )

    for ln in lines:
        m = row_re.match(ln)
        if not m:
            continue

        item_no = m.group(1)
        part_no = m.group(2)
        desc = m.group(3).strip()
        qty = parse_number(m.group(4))
        pounds = parse_number(m.group(5))
        unit_price = parse_number(m.group(6))
        extended = parse_number(m.group(7))

        items.append({
            "supplier_name": "Caterpillar Export Services",
            "product_name": desc,
            "product_id": part_no,
            "quantity": float(qty) if qty is not None else None,
            "unit_price": float(unit_price) if unit_price is not None else None,
            "total_price": float(extended) if extended is not None else None,
            # Optional: keep weight in name/notes later if you want; DB schema doesn't include it now
        })

    return items

# -----------------------------
# Generic fallback extraction (heuristics)
# -----------------------------
def normalize_sku(sku):
    if not sku or pd.isna(sku):
        return ''
    return str(sku).upper().replace('-', '').replace(' ', '').replace('_', '')

def extract_items_from_text(text, sku_example_hint=None):
    """Heuristic fallback for generic text-based quotations."""
    if not text or len(text.strip()) < 10:
        return []

    items = []
    lines = text.split('\n')
    supplier_name = "Unknown Supplier"

    for line in lines[:15]:
        if len(line.strip()) > 3 and "FACTURA" not in line.upper():
            supplier_name = line.strip()
            break

    header_keywords = ["DOCUMENTO", "RNC:", "CLIENTE:", "VENDEDOR:", "FECHA:", "TEL:", "PÁGINA", "CANT.", "PRECIO", "DESCRIPCIÓN"]
    sku_position_preference = None

    for line in lines:
        line = line.strip()
        if not line or len(line) < 10:
            continue
        if any(kw in line.upper() for kw in header_keywords):
            continue

        parts = line.split()
        if len(parts) < 3:
            continue

        trailing_numbers = []
        text_parts = []
        found_text = False

        for i in range(len(parts) - 1, -1, -1):
            val = parse_number(parts[i])
            if val is not None and 0 < val < 1000000 and not found_text:
                trailing_numbers.insert(0, val)
            else:
                found_text = True
                text_parts.insert(0, parts[i])

        if len(trailing_numbers) < 1:
            continue

        product_id = None
        clean_text_parts = [p.strip(':,.;()') for p in text_parts]

        if sku_example_hint:
            if sku_example_hint in text_parts:
                product_id = sku_example_hint
                idx = text_parts.index(sku_example_hint)
                if idx == 0:
                    sku_position_preference = 'first'
                elif idx == len(text_parts) - 1:
                    sku_position_preference = 'last'
            elif sku_example_hint in clean_text_parts:
                idx = clean_text_parts.index(sku_example_hint)
                product_id = clean_text_parts[idx]
                if idx == 0:
                    sku_position_preference = 'first'
                elif idx == len(clean_text_parts) - 1:
                    sku_position_preference = 'last'
            else:
                for part in text_parts:
                    if sku_example_hint in part:
                        product_id = sku_example_hint
                        break

        if product_id is None and sku_position_preference:
            candidate = None
            if sku_position_preference == 'first' and clean_text_parts:
                candidate = clean_text_parts[0]
            elif sku_position_preference == 'last' and clean_text_parts:
                candidate = clean_text_parts[-1]

            if candidate and len(candidate) >= 4 and (
                (any(c.isdigit() for c in candidate) and any(c.isalpha() for c in candidate)) or candidate.isdigit()
            ):
                product_id = candidate

        if product_id is None:
            for part in clean_text_parts:
                if len(part) >= 4 and (
                    (any(c.isdigit() for c in part) and any(c.isalpha() for c in part)) or part.isdigit()
                ):
                    product_id = part
                    break

        if product_id is None:
            continue

        desc_parts = []
        for p in text_parts:
            if product_id in p:
                continue
            desc_parts.append(p)

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
            "supplier_name": supplier_name,
            "product_name": description,
            "product_id": product_id,
            "quantity": qty,
            "unit_price": unit_price,
            "total_price": total_price
        })

    return items

# -----------------------------
# Gemini extraction (text-based)
# -----------------------------
def extract_with_llm(text, api_key, example_hints=None):
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-flash')

        hint_text = ""
        if example_hints and isinstance(example_hints, dict):
            hints = []
            if example_hints.get('sku'):
                hints.append(f"Look for '{example_hints['sku']}' as product_id.")
            if example_hints.get('item'):
                hints.append(f"Look for '{example_hints['item']}' as product_name.")
            if example_hints.get('qty'):
                hints.append(f"Look for '{example_hints['qty']}' as quantity.")
            if example_hints.get('price'):
                hints.append(f"Look for '{example_hints['price']}' as unit_price.")
            if hints:
                hint_text = "HINTS:\n" + "\n".join(hints)

        prompt = f"""
Return ONLY valid JSON with this schema:
{{"items":[{{"supplier_name":string,"product_name":string,"product_id":string|null,"quantity":number|null,"unit_price":number|null,"total_price":number|null}}]}}

Rules:
- If missing, use null (do not invent).
- No commentary. JSON only.
{hint_text}

Text:
{text or ""}
"""

        response = model.generate_content(prompt)
        content = (response.text or "").strip()

        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        data = json.loads(content)
        items = data.get("items", []) if isinstance(data, dict) else []
        return items if isinstance(items, list) else []
    except Exception as e:
        st.error(f"LLM Extraction failed: {str(e)}")
        return []

# -----------------------------
# Theme / CSS (kept minimal for brevity)
# -----------------------------
st.set_page_config(page_title="Cotización Rapida", page_icon="📊", layout="wide")

def inject_custom_css(theme='dark'):
    if theme == 'light':
        bg_color = "#ffffff"
        secondary_bg = "#f8f9fa"
        text_color = "#262730"
        border_color = "rgba(0, 0, 0, 0.08)"
        input_bg = "#ffffff"
        input_border = "#d3d3d3"
        accent_primary = "#2563eb"
        accent_secondary = "#059669"
        shadow_color = "rgba(0, 0, 0, 0.05)"
        hover_bg = "rgba(37, 99, 235, 0.05)"
        button_text = "#ffffff"
        button_gradient_start = "#2563eb"
        button_gradient_end = "#059669"
    else:
        bg_color = "#0e1117"
        secondary_bg = "#1e2530"
        text_color = "#fafafa"
        border_color = "rgba(255, 255, 255, 0.08)"
        input_bg = "#262730"
        input_border = "#4a4a4a"
        accent_primary = "#60a5fa"
        accent_secondary = "#34d399"
        shadow_color = "rgba(0, 0, 0, 0.3)"
        hover_bg = "rgba(96, 165, 250, 0.1)"
        button_text = "#ffffff"
        button_gradient_start = "#60a5fa"
        button_gradient_end = "#34d399"

    st.markdown(f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap');
        .stApp {{
            background-color: {bg_color} !important;
            color: {text_color} !important;
            font-family: 'Poppins', sans-serif !important;
        }}
        section[data-testid="stSidebar"] {{
            background: linear-gradient(180deg, {secondary_bg} 0%, {bg_color} 100%) !important;
            border-right: 1px solid {border_color};
        }}
        .stButton button {{
            background: linear-gradient(135deg, {button_gradient_start}, {button_gradient_end}) !important;
            color: {button_text} !important;
            border: none !important;
            border-radius: 10px !important;
            padding: 12px 28px !important;
            font-weight: 600 !important;
        }}
        </style>
    """, unsafe_allow_html=True)

# -----------------------------
# Database
# -----------------------------
def init_db():
    conn = sqlite3.connect('quotations.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS quotations 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute("PRAGMA table_info(quotations)")
    columns = [info[1] for info in c.fetchall()]
    if 'currency' not in columns:
        c.execute("ALTER TABLE quotations ADD COLUMN currency TEXT DEFAULT 'DOP'")
    if 'tax_rate' not in columns:
        c.execute("ALTER TABLE quotations ADD COLUMN tax_rate REAL DEFAULT 0.0")
    if 'discount_rate' not in columns:
        c.execute("ALTER TABLE quotations ADD COLUMN discount_rate REAL DEFAULT 0.0")

    c.execute('''CREATE TABLE IF NOT EXISTS items 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, quotation_id INTEGER, 
                  supplier_name TEXT, product_name TEXT, sku TEXT, quantity REAL, 
                  unit_price REAL, total_price REAL)''')
    conn.commit()
    conn.close()

def save_to_db(filename, items, currency='DOP', tax_rate=0.0, discount_rate=0.0):
    conn = sqlite3.connect('quotations.db')
    c = conn.cursor()
    c.execute(
        "INSERT INTO quotations (filename, currency, tax_rate, discount_rate) VALUES (?, ?, ?, ?)",
        (filename, currency, tax_rate, discount_rate)
    )
    quotation_id = c.lastrowid

    for item in items:
        qty = item.get('quantity') or 0
        unit = item.get('unit_price') or 0
        total = item.get('total_price') or (qty * unit)
        product_id = item.get('product_id', None)

        c.execute(
            "INSERT INTO items (quotation_id, supplier_name, product_name, sku, quantity, unit_price, total_price) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                quotation_id,
                item.get('supplier_name', 'Unknown'),
                item.get('product_name', 'Unknown'),
                product_id,
                qty,
                unit,
                total
            )
        )

    conn.commit()
    conn.close()
    return quotation_id

def get_items_by_quotation_id(quotation_id):
    conn = sqlite3.connect('quotations.db')
    try:
        items_df = pd.read_sql_query("SELECT * FROM items WHERE quotation_id = ?", conn, params=(quotation_id,))
        q_data = pd.read_sql_query("SELECT currency, tax_rate, discount_rate FROM quotations WHERE id = ?", conn, params=(quotation_id,))
        currency = 'DOP'
        tax_rate = 0.0
        discount_rate = 0.0
        if not q_data.empty:
            currency = q_data.iloc[0].get('currency', 'DOP') or 'DOP'
            tax_rate = q_data.iloc[0].get('tax_rate', 0.0) or 0.0
            discount_rate = q_data.iloc[0].get('discount_rate', 0.0) or 0.0

        items_list = items_df.to_dict('records')
        for item in items_list:
            item['currency'] = currency
            item['tax_rate'] = tax_rate
            item['discount_rate'] = discount_rate
        return items_list
    except Exception as e:
        st.error(f"Error fetching items: {e}")
        return []
    finally:
        conn.close()

def update_items_batch(edited_df):
    try:
        conn = sqlite3.connect('quotations.db')
        c = conn.cursor()

        for _, row in edited_df.iterrows():
            if 'id' in row and pd.notna(row['id']):
                c.execute("""
                    UPDATE items 
                    SET supplier_name=?, product_name=?, sku=?, quantity=?, unit_price=?, total_price=?
                    WHERE id=?
                """, (
                    row.get('supplier_name'),
                    row.get('product_name'),
                    row.get('sku'),
                    row.get('quantity'),
                    row.get('unit_price'),
                    row.get('total_price'),
                    int(row.get('id'))
                ))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Error updating database: {e}")
        return False

# -----------------------------
# App init / session state
# -----------------------------
init_db()

if 'session_items' not in st.session_state:
    st.session_state.session_items = []
if 'processed_files' not in st.session_state:
    st.session_state.processed_files = set()
if 'theme' not in st.session_state:
    st.session_state.theme = 'dark'
if 'file_supplier_format' not in st.session_state:
    st.session_state.file_supplier_format = {}

def clear_session():
    st.session_state.session_items = []
    st.session_state.processed_files = set()
    st.session_state.file_supplier_format = {}
    st.rerun()

inject_custom_css(st.session_state.theme)

# -----------------------------
# Sidebar
# -----------------------------
with st.sidebar:
    st.header("Session")
    if st.button("🆕 Start New Quotation", type="primary"):
        clear_session()

    st.divider()
    st.header("Settings")

    exchange_rate = st.number_input("💱 Exchange Rate (USD to DOP)", value=60.0, min_value=1.0, step=0.1, format="%.2f")
    st.info(f"1 USD = {exchange_rate} DOP")

    api_key = st.text_input("Gemini API Key (Optional)", type="password")

# -----------------------------
# File uploader
# -----------------------------
uploaded_files = st.file_uploader("Upload Quotations (PDF)", type="pdf", accept_multiple_files=True)

if uploaded_files:
    new_files = [f for f in uploaded_files if f.name not in st.session_state.processed_files]

    if new_files:
        if 'verification_index' not in st.session_state:
            st.session_state.verification_index = 0
        if 'file_hints' not in st.session_state:
            st.session_state.file_hints = {}
        if 'file_currencies' not in st.session_state:
            st.session_state.file_currencies = {}
        if 'file_taxes' not in st.session_state:
            st.session_state.file_taxes = {}
        if 'file_discounts' not in st.session_state:
            st.session_state.file_discounts = {}

        current_idx = st.session_state.verification_index

        if current_idx < len(new_files):
            preview_file = new_files[current_idx]

            with st.expander(f"🔍 Verify File {current_idx + 1}/{len(new_files)}: {preview_file.name}", expanded=True):

                # Supplier dropdown
                current_format = st.session_state.file_supplier_format.get(preview_file.name, "Auto (recommended)")
                selected_supplier_format = st.selectbox(
                    "🏷️ Supplier Format",
                    SUPPLIER_OPTIONS,
                    index=SUPPLIER_OPTIONS.index(current_format) if current_format in SUPPLIER_OPTIONS else 0,
                    key=f"supplier_format_{current_idx}",
                )
                st.session_state.file_supplier_format[preview_file.name] = selected_supplier_format

                # Currency selection
                current_currency = st.session_state.file_currencies.get(preview_file.name, "DOP")
                file_currency = st.radio("💵 Currency", ["DOP", "USD"], index=0 if current_currency == "DOP" else 1, key=f"currency_{current_idx}", horizontal=True)
                st.session_state.file_currencies[preview_file.name] = file_currency

                # Tax / Discount
                file_tax = st.number_input("Tax Rate (%)", 0.0, 100.0, float(st.session_state.file_taxes.get(preview_file.name, 0.0)), 0.1, key=f"tax_{current_idx}")
                st.session_state.file_taxes[preview_file.name] = file_tax

                file_discount = st.number_input("Discount Rate (%)", 0.0, 100.0, float(st.session_state.file_discounts.get(preview_file.name, 0.0)), 0.1, key=f"discount_{current_idx}")
                st.session_state.file_discounts[preview_file.name] = file_discount

                # Optional hints
                current_hints = st.session_state.file_hints.get(preview_file.name, {})
                col_h1, col_h2 = st.columns(2)
                with col_h1:
                    sku_hint = st.text_input("Example SKU (optional)", value=current_hints.get('sku', ''), key=f"hint_sku_{current_idx}")
                with col_h2:
                    price_hint = st.text_input("Example Price (optional)", value=current_hints.get('price', ''), key=f"hint_price_{current_idx}")

                st.session_state.file_hints[preview_file.name] = {"sku": sku_hint, "price": price_hint}

                # Test button
                if st.button("🧪 Test Extraction", key=f"test_{current_idx}"):
                    # IMPORTANT: PdfReader consumes file pointer; Streamlit UploadedFile needs seek(0)
                    preview_file.seek(0)
                    text = read_pdf_text(preview_file)

                    chosen = st.session_state.file_supplier_format.get(preview_file.name, "Auto (recommended)")
                    if chosen == "Auto (recommended)":
                        chosen = detect_supplier_format(text)

                    # Route
                    items = []
                    if chosen == "IMCA / Parts.Cat (Cart)":
                        items = extract_parts_cat_cart(text)
                    elif chosen == "Supplier B (Format B)":
                        items = extract_ces_quote(text)
                    else:
                        # Generic
                        if api_key and text:
                            items = extract_with_llm(text, api_key, example_hints=st.session_state.file_hints.get(preview_file.name, {}))
                        if not items and text:
                            items = extract_items_from_text(text, sku_example_hint=sku_hint)

                    if items:
                        st.success(f"✅ Extracted {len(items)} items (Format: {chosen})")
                        st.dataframe(pd.DataFrame(items))
                    else:
                        st.warning("No items extracted. If this is truly image-only, you’ll need OCR/Vision under 'Generic (Gemini/OCR)'.")
                        st.info("This particular CES quote format should extract via text if the PDF contains embedded text.")

                if st.button("✅ Confirm & Next", key=f"confirm_{current_idx}", type="primary"):
                    st.session_state.verification_index += 1
                    st.rerun()

            if st.button("⏩ Skip Verification & Process All"):
                st.session_state.verification_index = len(new_files)
                st.rerun()

        else:
            st.success(f"✅ All {len(new_files)} files verified! Ready to process.")

            if st.button("🚀 Process All Files", type="primary"):
                progress = st.progress(0)
                status = st.empty()

                for idx, f in enumerate(new_files):
                    status.text(f"Processing {f.name}...")
                    f.seek(0)
                    text = read_pdf_text(f)

                    chosen = st.session_state.file_supplier_format.get(f.name, "Auto (recommended)")
                    if chosen == "Auto (recommended)":
                        chosen = detect_supplier_format(text)

                    example_hints = st.session_state.file_hints.get(f.name, {})
                    sku_hint = example_hints.get("sku", "")

                    items = []
                    if chosen == "IMCA / Parts.Cat (Cart)":
                        items = extract_parts_cat_cart(text)
                    elif chosen == "Supplier B (Format B)":
                        items = extract_ces_quote(text)
                    else:
                        if api_key and text:
                            items = extract_with_llm(text, api_key, example_hints=example_hints)
                        if not items and text:
                            items = extract_items_from_text(text, sku_example_hint=sku_hint)

                    # Save
                    if items:
                        file_currency = st.session_state.file_currencies.get(f.name, "DOP")
                        file_tax = st.session_state.file_taxes.get(f.name, 0.0)
                        file_discount = st.session_state.file_discounts.get(f.name, 0.0)

                        q_id = save_to_db(f.name, items, currency=file_currency, tax_rate=file_tax, discount_rate=file_discount)
                        st.session_state.session_items.extend(get_items_by_quotation_id(q_id))
                        st.session_state.processed_files.add(f.name)
                        st.toast(f"✅ {f.name}: {len(items)} items ({chosen})")
                    else:
                        st.warning(f"{f.name}: no items extracted.")

                    progress.progress((idx + 1) / len(new_files))

                status.text("Done.")
                st.session_state.verification_index = 0
                st.session_state.file_hints = {}
                st.session_state.file_taxes = {}
                st.session_state.file_discounts = {}
                st.rerun()

# -----------------------------
# Results
# -----------------------------
if st.session_state.session_items:
    st.divider()
    df = pd.DataFrame(st.session_state.session_items)

    st.metric("Total Items", len(df))
    st.metric("Suppliers", df['supplier_name'].nunique() if 'supplier_name' in df.columns else 0)

    tab_review, tab_compare, tab_export = st.tabs(["📝 Review", "📊 Compare", "📤 Export"])

    with tab_review:
        st.subheader("Review / Edit")
        edited_df = st.data_editor(
            df.drop(columns=['quotation_id'], errors='ignore'),
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic"
        )
        if st.button("💾 Save Changes"):
            if update_items_batch(edited_df):
                st.session_state.session_items = edited_df.to_dict('records')
                st.success("Saved.")
                st.rerun()

    with tab_compare:
        st.subheader("Comparison (by SKU)")
        df2 = df.copy()
        df2['sku'] = df2['sku'].fillna('')
        df2 = df2[df2['sku'].str.len() >= 4].copy()
        if df2.empty:
            st.warning("No valid SKUs to compare.")
        else:
            df2['normalized_sku'] = df2['sku'].apply(normalize_sku)

            # Convert prices to DOP if needed
            def to_dop(row):
                unit = row.get('unit_price', 0.0) or 0.0
                cur = row.get('currency', 'DOP')
                if cur == 'USD':
                    return unit * exchange_rate
                return unit

            df2['unit_price_dop'] = df2.apply(to_dop, axis=1)
            pivot = df2.pivot_table(
                index=['normalized_sku', 'sku', 'product_name'],
                columns='supplier_name',
                values='unit_price_dop',
                aggfunc='min'
            ).reset_index()

            st.dataframe(pivot, use_container_width=True)

    with tab_export:
        st.subheader("Export")
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name="All Items")
        st.download_button(
            "📥 Download Excel",
            buffer.getvalue(),
            "quotation_export.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
else:
    st.info("Upload PDFs to start comparing.")
