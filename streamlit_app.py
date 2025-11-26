import streamlit as st
import pandas as pd
import sqlite3
import json
import os
from pypdf import PdfReader
import google.generativeai as genai
from io import BytesIO
import re

# Page Config
st.set_page_config(
    page_title="Quotation Compare",
    page_icon="📊",
    layout="wide"
)

# --- Database Functions ---
def init_db():
    conn = sqlite3.connect('quotations.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS quotations 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Check if 'currency' column exists in quotations, if not add it
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
    c.execute("INSERT INTO quotations (filename, currency, tax_rate, discount_rate) VALUES (?, ?, ?, ?)", (filename, currency, tax_rate, discount_rate))
    quotation_id = c.lastrowid
    
    for item in items:
        qty = item.get('quantity') or 0
        price = item.get('unit_price') or 0
        total = item.get('total_price') or (qty * price)
        product_id = item.get('product_id', None)
        
        c.execute("INSERT INTO items (quotation_id, supplier_name, product_name, sku, quantity, unit_price, total_price) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (quotation_id, item.get('supplier_name', 'Unknown'), item.get('product_name', 'Unknown'), 
                   product_id, qty, price, total))
    conn.commit()
    conn.close()
    return quotation_id

def get_latest_quotation_items():
    conn = sqlite3.connect('quotations.db')
    # Use pandas for easier dataframe handling
    try:
        # Get latest quotation ID
        latest_q = pd.read_sql_query("SELECT id FROM quotations ORDER BY upload_date DESC LIMIT 1", conn)
        if not latest_q.empty:
            q_id = latest_q.iloc[0]['id']
            items = pd.read_sql_query("SELECT * FROM items WHERE quotation_id = ?", conn, params=(int(q_id),))
            return items
        else:
            return pd.DataFrame()
    except Exception as e:
        st.error(f"Database error: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def get_items_by_quotation_id(quotation_id):
    """Fetch all items for a specific quotation ID, returns list of dicts with IDs"""
    conn = sqlite3.connect('quotations.db')
    try:
        items_df = pd.read_sql_query("SELECT * FROM items WHERE quotation_id = ?", conn, params=(quotation_id,))
        
        # Get currency, tax, and discount for this quotation
        q_data = pd.read_sql_query("SELECT currency, tax_rate, discount_rate FROM quotations WHERE id = ?", conn, params=(quotation_id,))
        currency = 'DOP'
        tax_rate = 0.0
        discount_rate = 0.0
        if not q_data.empty:
            if 'currency' in q_data.columns:
                currency = q_data.iloc[0]['currency']
            if 'tax_rate' in q_data.columns:
                tax_rate = q_data.iloc[0]['tax_rate'] or 0.0
            if 'discount_rate' in q_data.columns:
                discount_rate = q_data.iloc[0]['discount_rate'] or 0.0
            
        items_list = items_df.to_dict('records')
        # Attach currency, tax, and discount to each item
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


# --- Parsing Logic (Ported from app.py) ---
def parse_number(num_str):
    if not isinstance(num_str, str): return num_str
    clean_str = num_str.upper().replace('$', '').replace('€', '').replace('S/', '').replace('USD', '').replace('EUR', '').strip()
    clean_str = clean_str.replace(' ', '')
    if not clean_str: return None

    try:
        if ',' in clean_str and '.' in clean_str:
            last_comma = clean_str.rfind(',')
            last_dot = clean_str.rfind('.')
            if last_comma > last_dot: # 1.234,56
                clean_str = clean_str.replace('.', '').replace(',', '.')
            else: # 1,234.56
                clean_str = clean_str.replace(',', '')
        elif ',' in clean_str: 
            parts = clean_str.split(',')
            if len(parts[-1]) == 2: clean_str = clean_str.replace(',', '.')
            elif len(parts[-1]) == 3: clean_str = clean_str.replace(',', '')
            else: clean_str = clean_str.replace(',', '.')
        return float(clean_str)
    except ValueError:
        return None

def extract_with_llm(text, api_key, sku_header_hint=None):
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        hint_text = ""
        if sku_header_hint:
            hint_text = f"IMPORTANT: The SKU/Product ID is located under the column header '{sku_header_hint}'. Prioritize this column for the 'product_id' field."
        
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
            # Check if product_id exists and is at least 6 chars
            if pid and isinstance(pid, str) and len(pid.strip()) >= 6:
                valid_items.append(item)
                
        return valid_items
    except Exception as e:
        st.error(f"LLM Extraction failed: {str(e)}")
        return []


# --- SKU Normalization ---
def normalize_sku(sku):
    """Normalize SKU for comparison by removing hyphens, spaces, and converting to uppercase."""
    if not sku or pd.isna(sku):
        return ''
    # Convert to string, uppercase, remove hyphens and spaces
    normalized = str(sku).upper().replace('-', '').replace(' ', '').replace('_', '')
    return normalized

def extract_items_from_text(text, sku_header_hint=None):
    # Simplified heuristic fallback (same logic as app.py but condensed)
    if not text or len(text.strip()) < 10: return []
    items = []
    lines = text.split('\n')
    supplier_name = "Unknown Supplier"
    # Basic supplier detection
    for line in lines[:15]:
        if len(line.strip()) > 3 and "FACTURA" not in line.upper():
            supplier_name = line.strip()
            break
            
    header_keywords = ["DOCUMENTO", "RNC:", "CLIENTE:", "VENDEDOR:", "FECHA:", "TEL:", "PÁGINA", "CANT.", "PRECIO", "DESCRIPCIÓN"]
    
    sku_position_preference = None # 'first' or 'last'
    
    for line in lines:
        line = line.strip()
        if not line or len(line) < 10: continue
        if any(kw in line.upper() for kw in header_keywords): continue
        
        parts = line.split()
        if len(parts) < 3: continue
        
        # Right-to-left number scanning
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
        
        if len(trailing_numbers) >= 1:
            # Extract logic - SKU must be at least 6 characters
            product_id = None
            desc_parts = []
            
            # Clean parts for comparison (remove attached punctuation like colons)
            clean_text_parts = [p.strip(':,.;()') for p in text_parts]
            
            # Check if hint is in this line to learn position
            if sku_header_hint:
                # Check exact match or match with stripped punctuation
                if sku_header_hint in text_parts:
                    product_id = sku_header_hint
                    if text_parts.index(sku_header_hint) == 0:
                        sku_position_preference = 'first'
                    elif text_parts.index(sku_header_hint) == len(text_parts) - 1:
                        sku_position_preference = 'last'
                elif sku_header_hint in clean_text_parts:
                    idx = clean_text_parts.index(sku_header_hint)
                    product_id = clean_text_parts[idx] # Use the clean version
                    if idx == 0:
                        sku_position_preference = 'first'
                    elif idx == len(clean_text_parts) - 1:
                        sku_position_preference = 'last'
                else:
                    # Substring match (e.g. "4J-0522:Sello")
                    for part in text_parts:
                        if sku_header_hint in part:
                            product_id = sku_header_hint
                            break
            
            # If we haven't found it yet, try using learned preference
            if product_id is None and sku_position_preference:
                candidate = None
                if sku_position_preference == 'first' and clean_text_parts:
                    candidate = clean_text_parts[0]
                elif sku_position_preference == 'last' and clean_text_parts:
                    candidate = clean_text_parts[-1]
                
                # Verify candidate is valid SKU
                if candidate and len(candidate) >= 6 and (
                    (any(c.isdigit() for c in candidate) and any(c.isalpha() for c in candidate)) or
                    candidate.isdigit() or
                    (candidate.isupper() and candidate.isalnum())
                ):
                    product_id = candidate

            # Fallback / Default Search
            if product_id is None:
                for i, part in enumerate(clean_text_parts):
                    # Check if this could be a SKU: alphanumeric OR numeric, and at least 6 characters
                    # We allow all-digit SKUs now as long as they are long enough
                    if product_id is None and len(part) >= 6 and (
                        (any(c.isdigit() for c in part) and any(c.isalpha() for c in part)) or # Alphanumeric
                        part.isdigit() # All digits
                    ):
                        product_id = part
                    else:
                        pass
            
            # Re-build description based on what we picked as SKU
            # We need to use the original text_parts for description, but exclude the SKU part
            if product_id:
                # Find which part was the SKU (fuzzy match due to cleaning/substring)
                desc_parts = []
                for p in text_parts:
                    # If the part contains the SKU, it's the SKU part (or attached to it)
                    if product_id in p:
                        continue
                    desc_parts.append(p)
            else:
                desc_parts = text_parts

            # ONLY create item if we found a valid SKU
            if product_id is None:
                continue  # Skip this line if no valid SKU found
            
            description = " ".join(desc_parts[:20]) if desc_parts else " ".join(text_parts[:20])
            
            qty = 1.0
            unit_price = 0.0
            total_price = 0.0
            
            if len(trailing_numbers) >= 3:
                qty = trailing_numbers[0]
                unit_price = trailing_numbers[1]
                total_price = trailing_numbers[-1] # Assume last is total
            elif len(trailing_numbers) == 2:
                unit_price = trailing_numbers[0]
                total_price = trailing_numbers[1]
            elif len(trailing_numbers) == 1:
                # Only one number found, assume it's the total price
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

def update_items_batch(edited_df):
    try:
        conn = sqlite3.connect('quotations.db')
        c = conn.cursor()
        
        # Update items in database
        for index, row in edited_df.iterrows():
            # Only update if ID exists (it should)
            if 'id' in row and pd.notna(row['id']):
                c.execute("""
                    UPDATE items 
                    SET supplier_name=?, product_name=?, sku=?, quantity=?, unit_price=?, total_price=?
                    WHERE id=?
                """, (row['supplier_name'], row['product_name'], row['sku'], 
                      row['quantity'], row['unit_price'], row['total_price'], row['id']))
            
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Error updating database: {e}")
        return False

# --- Main UI ---
init_db()

# --- Session State Management ---
if 'session_items' not in st.session_state:
    st.session_state.session_items = []
if 'processed_files' not in st.session_state:
    st.session_state.processed_files = set()

def clear_session():
    st.session_state.session_items = []
    st.session_state.processed_files = set()
    st.rerun()

# --- Main UI ---
init_db()

st.title("📊 Quotation Compare")

with st.sidebar:
    st.header("Session")
    if st.button("🆕 Start New Quotation", type="primary", help="Clear current data and start over"):
        clear_session()
    
    st.divider()
    st.header("Settings")
    
    # Exchange Rate Input
    exchange_rate = st.number_input(
        "💱 Exchange Rate (USD to DOP)", 
        value=60.0, 
        min_value=1.0, 
        step=0.1, 
        format="%.2f",
        help="Current exchange rate from US Dollars to Dominican Pesos"
    )
    st.info(f"1 USD = {exchange_rate} DOP")
    
    api_key = st.text_input("Gemini API Key (Optional)", type="password", help="For smart parsing using Google Gemini")
    
    st.divider()
    st.info("Upload multiple PDFs to compare prices across suppliers.")

# File Uploader
uploaded_files = st.file_uploader("Upload Quotations (PDF)", type="pdf", accept_multiple_files=True)

if uploaded_files:
    # Process new files
    new_files = [f for f in uploaded_files if f.name not in st.session_state.processed_files]
    
    if new_files:
        # Initialize verification state
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
        
        # Check if verification is complete
        if current_idx < len(new_files):
            # --- Multi-File Preview Section ---
            with st.expander(f"🔍 Verify File {current_idx + 1}/{len(new_files)}: {new_files[current_idx].name}", expanded=True):
                st.write("Run a test to verify the SKU extraction for this file.")
                
                preview_file = new_files[current_idx]
                
                # Currency Selection
                current_currency = st.session_state.file_currencies.get(preview_file.name, "DOP")
                file_currency = st.radio(
                    "💵 Select Currency for this quotation:",
                    ["DOP", "USD"],
                    index=0 if current_currency == "DOP" else 1,
                    key=f"currency_{current_idx}",
                    horizontal=True,
                    help="Dominican Pesos (DOP) or US Dollars (USD)"
                )
                st.session_state.file_currencies[preview_file.name] = file_currency
                
                # Tax Input
                current_tax = st.session_state.file_taxes.get(preview_file.name, 0.0)
                file_tax = st.number_input(
                    "Add Tax Rate (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(current_tax),
                    step=0.1,
                    format="%.1f",
                    key=f"tax_{current_idx}",
                    help="Enter tax percentage (e.g., 18 for 18%). This will be applied to all item prices."
                )
                st.session_state.file_taxes[preview_file.name] = file_tax
                
                # Discount Input
                current_discount = st.session_state.file_discounts.get(preview_file.name, 0.0)
                file_discount = st.number_input(
                    "Add Discount Rate (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(current_discount),
                    step=0.1,
                    format="%.1f",
                    key=f"discount_{current_idx}",
                    help="Enter discount percentage (e.g., 10 for 10%). This is applied BEFORE tax."
                )
                st.session_state.file_discounts[preview_file.name] = file_discount
                
                # Get existing hint for this file (if re-visiting)
                file_hint_key = f"hint_{preview_file.name}"
                current_hint = st.session_state.file_hints.get(preview_file.name, "")
                
                sku_hint_input = st.text_input("SKU Header Hint OR Example SKU (if extraction is wrong)", 
                                             value=current_hint,
                                             placeholder="e.g. 'Codigo' OR '4J-0522'",
                                             help="Enter the column header name OR a specific SKU from the file to help the extractor.",
                                             key=file_hint_key)
                
                col1, col2 = st.columns([1, 1])
                
                with col1:
                    if st.button(f"Test '{preview_file.name}'", key=f"test_{current_idx}"):
                        with st.spinner("Extracting preview (scanning all pages)..."):
                            try:
                                reader = PdfReader(preview_file)
                                preview_items = []
                                found_on_page = -1
                                debug_text = ""
                                
                                # Scan pages until we find items
                                for page_num, page in enumerate(reader.pages):
                                    text = page.extract_text()
                                    # Collect debug text from first 3 pages
                                    if page_num < 3:
                                        debug_text += f"--- Page {page_num+1} ---\n{text[:500]}\n\n"
                                    
                                    # Run extraction with hint
                                    page_items = []
                                    if api_key:
                                        page_items = extract_with_llm(text, api_key, sku_header_hint=sku_hint_input)
                                    else:
                                        page_items = extract_items_from_text(text, sku_header_hint=sku_hint_input)
                                    
                                    if page_items:
                                        preview_items = page_items
                                        found_on_page = page_num + 1
                                        break # Stop at first page with items
                                
                                if preview_items:
                                    st.success(f"✅ Found {len(preview_items)} items on Page {found_on_page}")
                                    st.dataframe(pd.DataFrame(preview_items)[['product_id', 'product_name', 'unit_price', 'quantity']])
                                    st.success("Check the 'product_id' column. If it's wrong, enter a hint above and re-test.")
                                else:
                                    st.warning("No items found in any page.")
                                    with st.expander("Debug Raw Text (First 3 Pages)"):
                                        st.text(debug_text)
                                    
                            except Exception as e:
                                st.error(f"Preview failed: {e}")
                
                with col2:
                    if st.button("✅ Confirm & Next", key=f"confirm_{current_idx}", type="primary"):
                        # Save hint for this file
                        st.session_state.file_hints[preview_file.name] = sku_hint_input
                        # Move to next file
                        st.session_state.verification_index += 1
                        st.rerun()
            
            # Show skip button
            if st.button("⏩ Skip Verification & Process All", key="skip_verification"):
                st.session_state.verification_index = len(new_files)
                st.rerun()
        
        else:
            # All files verified, show process button
            st.success(f"✅ All {len(new_files)} files verified! Ready to process.")
            
            if st.button("🚀 Process All Files", type="primary"):
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                for idx, uploaded_file in enumerate(new_files):
                    status_text.text(f"Processing {uploaded_file.name}...")
                    try:
                        reader = PdfReader(uploaded_file)
                        text = ""
                        for page in reader.pages:
                            text += page.extract_text() + "\n"
                        
                        items = []
                        # Default supplier name to filename (minus extension)
                        default_supplier = os.path.splitext(uploaded_file.name)[0]
                        
                        # Get hint and currency for this specific file
                        file_hint = st.session_state.file_hints.get(uploaded_file.name, "")
                        file_currency = st.session_state.file_currencies.get(uploaded_file.name, "DOP")
                        file_tax = st.session_state.file_taxes.get(uploaded_file.name, 0.0)
                        file_discount = st.session_state.file_discounts.get(uploaded_file.name, 0.0)
                        
                        if api_key:
                            items = extract_with_llm(text, api_key, sku_header_hint=file_hint)
                            if not items:
                                items = extract_items_from_text(text, sku_header_hint=file_hint)
                        else:
                            items = extract_items_from_text(text, sku_header_hint=file_hint)
                        
                        if items:
                            # Normalize and tag with supplier
                            for item in items:
                                if not item.get('supplier_name') or item.get('supplier_name') == "Unknown Supplier":
                                    item['supplier_name'] = default_supplier
                                
                                # Save to DB for persistence
                                # We create a new quotation record for each file
                                # This is a bit inefficient (one by one), but reuses existing logic
                            
                            # Save with currency, tax, and discount
                            q_id = save_to_db(uploaded_file.name, items, currency=file_currency, tax_rate=file_tax, discount_rate=file_discount)
                            
                            # Fetch items with currency attached
                            new_db_items_list = get_items_by_quotation_id(q_id)
                            
                            st.session_state.session_items.extend(new_db_items_list)
                            st.session_state.processed_files.add(uploaded_file.name)
                            st.toast(f"✅ Extracted {len(items)} items from {uploaded_file.name}")
                        else:
                            st.warning(f"Could not extract items from {uploaded_file.name}")
                    
                    except Exception as e:
                        st.error(f"Error processing {uploaded_file.name}: {e}")
                    
                    progress_bar.progress((idx + 1) / len(new_files))
                
                status_text.text("Processing complete!")
                # Reset verification state
                st.session_state.verification_index = 0
                st.session_state.file_hints = {}
                st.session_state.file_taxes = {}
                st.session_state.file_discounts = {}
                st.rerun()


# --- Results Area ---
st.divider()

if st.session_state.session_items:
    st.subheader(f"Combined Quotations ({len(st.session_state.session_items)} items)")
    
    # Convert session items to DataFrame
    df = pd.DataFrame(st.session_state.session_items)
    
    # Prepare DF for editor (hide internal quotation_id)
    editor_df = df.drop(columns=['quotation_id'], errors='ignore')

    # Editable Dataframe
    edited_df = st.data_editor(
        editor_df,
        column_config={
            "id": None,  # Hide ID column
            "supplier_name": "Supplier",
            "product_name": "Product",
            "sku": "Product ID",
            "quantity": st.column_config.NumberColumn("Qty", format="%.2f"),
            "unit_price": st.column_config.NumberColumn("Price", format="$%.2f"),
            "total_price": st.column_config.NumberColumn("Total", format="$%.2f"),
        },
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="data_editor"
    )

    if st.button("💾 Save Changes", type="primary"):
        if update_items_batch(edited_df):
            st.success("Changes saved successfully!")
            # Update session state with edited data
            # We need to reload from DB or just update in place?
            # update_items_batch updates DB. We should reload to be safe and get consistent state.
            # But reloading ALL items might be tricky if we don't track which quotation IDs are in session.
            # Let's just update the session_items with the edited_df content for now.
            st.session_state.session_items = edited_df.to_dict('records')
            st.rerun()
    
    # --- Comparison & Export ---
    st.divider()
    st.subheader("Price Comparison")
    
    # Comparison Logic
    if not df.empty:
        # Ensure we have strings
        df['sku'] = df['sku'].fillna('')
        df['product_name'] = df['product_name'].fillna('Unknown Product')
        
        # Filter: Only include rows with SKUs that are at least 6 characters
        df_filtered = df[df['sku'].str.len() >= 6].copy()
        
        if df_filtered.empty:
            st.warning("No items with SKUs of 6+ characters found for comparison.")
        else:
            # Add normalized SKU for grouping (fuzzy matching)
            df_filtered['normalized_sku'] = df_filtered['sku'].apply(normalize_sku)
        
        # Simpler base SKU grouping: find shortest matching prefix
        def find_base_sku(norm_sku, all_norm_skus):
            """Find the base SKU by checking which SKUs contain or are contained by this one"""
            candidates = [norm_sku]
            for other_sku in all_norm_skus:
                if not other_sku:
                    continue
                # Check if one is a substring of the other
                if norm_sku in other_sku or other_sku in norm_sku:
                    candidates.append(other_sku)
            # Return the shortest one as the base
            return min(candidates, key=len) if candidates else norm_sku
        
        # Get all unique normalized SKUs
        all_norm_skus = df_filtered['normalized_sku'].unique()
        
        # Apply base SKU mapping
        df_filtered['base_sku'] = df_filtered['normalized_sku'].apply(lambda x: find_base_sku(x, all_norm_skus))
        
        # Sort by base SKU and supplier name
        df_sorted = df_filtered.sort_values(['base_sku', 'supplier_name'])
        
        # Apply Currency Conversion (Convert all to DOP for comparison)
        def convert_price_to_dop(row):
            """Convert price to DOP based on currency, apply discount then tax"""
            price = row['unit_price']
            currency = row.get('currency', 'DOP')
            tax_rate = row.get('tax_rate', 0.0)
            discount_rate = row.get('discount_rate', 0.0)
            
            # 1. Apply Discount
            price_after_discount = price * (1 - (discount_rate / 100.0))
            
            # 2. Apply Tax
            price_with_tax = price_after_discount * (1 + (tax_rate / 100.0))
            
            if currency == 'USD':
                return price_with_tax * exchange_rate
            return price_with_tax

        def convert_total_to_dop(row):
            """Convert total to DOP based on currency, apply discount then tax"""
            total = row['total_price']
            currency = row.get('currency', 'DOP')
            tax_rate = row.get('tax_rate', 0.0)
            discount_rate = row.get('discount_rate', 0.0)
            
            # 1. Apply Discount
            total_after_discount = total * (1 - (discount_rate / 100.0))
            
            # 2. Apply Tax
            total_with_tax = total_after_discount * (1 + (tax_rate / 100.0))
            
            if currency == 'USD':
                return total_with_tax * exchange_rate
            return total_with_tax

        df_sorted['unit_price_dop'] = df_sorted.apply(convert_price_to_dop, axis=1)
        df_sorted['total_price_dop'] = df_sorted.apply(convert_total_to_dop, axis=1)
        
        # Build display DataFrame with blank rows between SKU groups
        display_rows = []
        grouped = df_sorted.groupby('base_sku', sort=False)
        
        for idx, (base_sku, group) in enumerate(grouped):
            if not base_sku:
                continue
            
            # Add all rows from this SKU group
            for _, row in group.iterrows():
                display_rows.append({
                    'id': row['id'],  # Include ID for tracking
                    'quotation_id': row['quotation_id'], # Include quotation_id for Summary grouping
                    'SKU': row['sku'],
                    'Product Name': row['product_name'],
                    'Supplier Name': row['supplier_name'],
                    'Currency': row.get('currency', 'DOP'),  # Show original currency
                    'Quantity': row['quantity'],
                    'Price': row['unit_price_dop'],  # Use converted price
                    'Final Price': row['total_price_dop'],  # Use converted total
                    'Tax Rate': f"{row.get('tax_rate', 0.0)}%",
                    'Discount Rate': f"{row.get('discount_rate', 0.0)}%"
                })
            
            # Add a blank row after each group (except the last one)
            display_rows.append({
                'id': None,
                'quotation_id': None,
                'SKU': '',
                'Product Name': '',
                'Supplier Name': '',
                'Currency': None,
                'Quantity': None,
                'Price': None,
                'Final Price': None,
                'Tax Rate': None,
                'Discount Rate': None
            })
        
        # Remove the very last blank row if it exists
        if display_rows and display_rows[-1]['SKU'] == '':
            display_rows.pop()
            
        comparison_display_df = pd.DataFrame(display_rows)
        
        # Display Editable Table
        st.markdown("### 📝 Price Comparison (Editable)")
        st.info(f"💡 All prices shown in **DOP** (converted at rate: 1 USD = {exchange_rate} DOP). Edit values directly or delete rows as needed.")
        
        edited_comparison = st.data_editor(
            comparison_display_df,
            key="price_comparison_editor",
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "id": None, # Hide ID column
                "quotation_id": None, # Hide quotation_id column
                "SKU": st.column_config.TextColumn("SKU", width="medium"),
                "Product Name": st.column_config.TextColumn("Product Name", width="large"),
                "Supplier Name": st.column_config.TextColumn("Supplier Name", width="large"),
                "Currency": st.column_config.TextColumn("Orig. Currency", width="small", help="Original quotation currency"),
                "Quantity": st.column_config.NumberColumn("Quantity", format="%.2f"),
                "Price": st.column_config.NumberColumn("Price (Net)", format="$%.2f", help="After discount and tax"),
                "Final Price": st.column_config.NumberColumn("Final Price (Net)", format="$%.2f", help="After discount and tax"),
                "Tax Rate": st.column_config.TextColumn("Tax", width="small"),
                "Discount Rate": st.column_config.TextColumn("Discount", width="small"),
            }
        )
            
        # Save changes button
        if st.button("💾 Save Comparison Changes", type="primary", key="save_comparison"):
            # Find deleted rows (IDs in original but not in edited)
            original_ids = set(comparison_display_df[comparison_display_df['id'].notna()]['id'])
            current_ids = set(edited_comparison[edited_comparison['id'].notna()]['id'])
            deleted_ids = original_ids - current_ids
            
            # Delete from DB
            if deleted_ids:
                conn = sqlite3.connect('quotations.db')
                c = conn.cursor()
                for del_id in deleted_ids:
                    c.execute("DELETE FROM items WHERE id = ?", (int(del_id),))
                conn.commit()
                conn.close()
                st.success(f"Deleted {len(deleted_ids)} items")
            
            # Update rows (if price/qty changed)
            # Prepare data for update (Reverse mapping and conversion)
            updates_df = edited_comparison[edited_comparison['id'].notna()].copy()
            
            # Rename columns to match DB schema
            updates_df = updates_df.rename(columns={
                'SKU': 'sku',
                'Product Name': 'product_name',
                'Supplier Name': 'supplier_name',
                'Quantity': 'quantity'
            })
            
            # Reverse currency conversion for Price and Total
            def reverse_price(row):
                price = row['Price']
                currency = row.get('Currency', 'DOP')
                tax_str = str(row.get('Tax Rate', '0')).replace('%', '')
                discount_str = str(row.get('Discount Rate', '0')).replace('%', '')
                try:
                    tax_rate = float(tax_str)
                    discount_rate = float(discount_str)
                except:
                    tax_rate = 0.0
                    discount_rate = 0.0
                
                # Reverse exchange rate
                if currency == 'USD' and exchange_rate > 0:
                    price = price / exchange_rate
                
                # Reverse tax
                price = price / (1 + (tax_rate / 100.0))
                
                # Reverse discount (Price_Net = Price_Gross * (1 - Discount/100))
                # Price_Gross = Price_Net / (1 - Discount/100)
                if discount_rate < 100:
                    price = price / (1 - (discount_rate / 100.0))
                    
                return price

            def reverse_total(row):
                total = row['Final Price']
                currency = row.get('Currency', 'DOP')
                tax_str = str(row.get('Tax Rate', '0')).replace('%', '')
                discount_str = str(row.get('Discount Rate', '0')).replace('%', '')
                try:
                    tax_rate = float(tax_str)
                    discount_rate = float(discount_str)
                except:
                    tax_rate = 0.0
                    discount_rate = 0.0
                
                # Reverse exchange rate
                if currency == 'USD' and exchange_rate > 0:
                    total = total / exchange_rate
                
                # Reverse tax
                total = total / (1 + (tax_rate / 100.0))
                
                # Reverse discount
                if discount_rate < 100:
                    total = total / (1 - (discount_rate / 100.0))
                    
                return total

            updates_df['unit_price'] = updates_df.apply(reverse_price, axis=1)
            updates_df['total_price'] = updates_df.apply(reverse_total, axis=1)
            
            # Update items
            update_items_batch(updates_df)
            
            # Reload session_items from DB to reflect changes
            all_saved_items = []
            for filename in st.session_state.processed_files:
                # Get quotation_id(s) for this filename
                conn = sqlite3.connect('quotations.db')
                q_ids = pd.read_sql_query("SELECT id FROM quotations WHERE filename = ?", conn, params=(filename,))
                for q_id in q_ids['id']:
                    items = get_items_by_quotation_id(q_id)
                    all_saved_items.extend(items)
                conn.close()
            
            st.session_state.session_items = all_saved_items
            st.success("✅ Changes saved successfully!")
            st.rerun()
        
        
        # Export Buttons
        col1, col2 = st.columns(2)
        
        with col1:
             # CSV Export (Combined)
            csv = edited_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                "📥 Download Combined CSV",
                csv,
                "combined_quotations.csv",
                "text/csv",
                key='download-csv'
            )
            
        with col2:
            # Excel Export (Multi-sheet)
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                # Sheet 1: Summary (Lowest Price per SKU)
                if 'comparison_display_df' in locals() and not comparison_display_df.empty:
                    # Filter for lowest price rows
                    summary_rows = []
                    current_sku = None
                    group_rows = []
                    
                    # We need to iterate the display DF to find groups (separated by blank rows)
                    # Note: comparison_display_df has blank rows as NaN/empty strings
                    
                    # Helper to get numeric price
                    def get_final_price(row):
                        try:
                            return float(row['Final Price'])
                        except:
                            return float('inf')

                    for _, row in comparison_display_df.iterrows():
                        sku = row['SKU']
                        price = row['Price']
                        
                        # Check if blank row
                        is_blank = (pd.isna(price) or price == '') and (pd.isna(sku) or sku == '')
                        
                        if is_blank:
                            if group_rows:
                                # Find min price in group
                                min_row = min(group_rows, key=get_final_price)
                                summary_rows.append(min_row)
                                group_rows = []
                        else:
                            group_rows.append(row)
                    
                    # Process last group
                    if group_rows:
                        min_row = min(group_rows, key=get_final_price)
                        summary_rows.append(min_row)
                    
                    if summary_rows:
                        summary_df = pd.DataFrame(summary_rows)
                        # Remove ID column if present
                        summary_df = summary_df.drop(columns=['id'], errors='ignore')
                        
                        # Add placeholder columns
                        summary_df['Tax'] = ''
                        summary_df['Transportation Cost'] = ''
                        summary_df['Complete Price'] = ''
                        
                        # Calculate Grand Total
                        grand_total = summary_df['Final Price'].sum()
                        
                        # Write Main Summary Table
                        summary_df.to_excel(writer, index=False, sheet_name='Summary', startrow=0)
                        
                        # Write Grand Total Row
                        worksheet_summary = writer.sheets['Summary']
                        last_row = len(summary_df) + 2 # Header + Data + 1 (1-based)
                        worksheet_summary.cell(row=last_row, column=1, value="GRAND TOTAL")
                        # Find 'Final Price' column index (it's the last one from original data + placeholders)
                        # Columns: SKU, Product Name, Supplier Name, Quantity, Price, Final Price, Tax, Trans, Complete
                        # Index: 1, 2, 3, 4, 5, 6, 7, 8, 9
                        final_price_col = list(summary_df.columns).index('Final Price') + 1
                        worksheet_summary.cell(row=last_row, column=final_price_col, value=grand_total)
                        
                        # Bold the total row
                        from openpyxl.styles import Font
                        bold_font = Font(bold=True)
                        for col in range(1, len(summary_df.columns) + 1):
                            worksheet_summary.cell(row=last_row, column=col).font = bold_font
                        
                        # --- Quotation File Breakdowns (Vendor Level) ---
                        current_row = last_row + 3 # Leave 2 blank rows
                        
                        # We group by quotation_id to represent the Vendor/File
                        # summary_df needs to have quotation_id. 
                        # We added it to comparison_display_df, so it should be here.
                        
                        if 'quotation_id' in summary_df.columns:
                            conn = sqlite3.connect('quotations.db')
                            unique_q_ids = summary_df['quotation_id'].unique()
                            
                            for q_id in unique_q_ids:
                                if pd.isna(q_id): continue
                                
                                # Get filename (Vendor Name)
                                q_data = pd.read_sql_query("SELECT filename FROM quotations WHERE id = ?", conn, params=(int(q_id),))
                                if q_data.empty: continue
                                
                                filename = q_data.iloc[0]['filename']
                                vendor_name = os.path.splitext(filename)[0]
                                
                                # Filter items for this quotation
                                vendor_df = summary_df[summary_df['quotation_id'] == q_id].copy()
                                
                                # Drop internal columns for display
                                display_vendor_df = vendor_df.drop(columns=['quotation_id'], errors='ignore')
                                
                                vendor_total = vendor_df['Final Price'].sum()
                                
                                # Write Header
                                worksheet_summary.cell(row=current_row, column=1, value=f"Winning Items from: {vendor_name}")
                                worksheet_summary.cell(row=current_row, column=1).font = bold_font
                                current_row += 1
                                
                                # Write Table
                                display_vendor_df.to_excel(writer, index=False, sheet_name='Summary', startrow=current_row-1, header=True)
                                
                                # Write Total Row
                                table_end_row = current_row + len(display_vendor_df) + 1
                                worksheet_summary.cell(row=table_end_row, column=1, value="TOTAL")
                                worksheet_summary.cell(row=table_end_row, column=final_price_col, value=vendor_total)
                                for col in range(1, len(display_vendor_df.columns) + 1):
                                    worksheet_summary.cell(row=table_end_row, column=col).font = bold_font
                                
                                current_row = table_end_row + 3 # Leave space for next table
                            conn.close()
                        
                        # Auto-size Summary columns (based on main table)
                        for idx, col in enumerate(summary_df.columns):
                            if col == 'quotation_id': continue
                            max_len = len(str(col)) + 2
                            col_max = summary_df[col].astype(str).str.len().max()
                            if pd.notna(col_max):
                                max_len = max(max_len, col_max + 2)
                            worksheet_summary.column_dimensions[chr(65 + idx)].width = min(max_len, 50)

                # Sheet 2: Price Comparison (Default view)
                # Use the same DataFrame we built for the UI display to ensure consistency
                if 'comparison_display_df' in locals() and not comparison_display_df.empty:
                    # Export without the id column
                    export_df = comparison_display_df.drop(columns=['id'], errors='ignore')
                    export_df.to_excel(writer, index=False, sheet_name='Price Comparison')
                    
                    # Get worksheet for styling
                    worksheet = writer.sheets['Price Comparison']
                    from openpyxl.styles import PatternFill
                    
                    # Light green fill for highlighting
                    green_fill = PatternFill(start_color='90EE90', end_color='90EE90', fill_type='solid')
                    
                    # Find SKU groups and highlight lowest price
                    group_rows = []
                    
                    for row_idx, row_data in enumerate(export_df.iterrows(), start=2):  # Excel rows start at 2 (after header)
                        _, row = row_data
                        sku = row['SKU']
                        
                        # Check if this is a blank row (separator)
                        is_blank = pd.isna(row['Price']) and (pd.isna(sku) or sku == '')
                        
                        if is_blank:
                            # End of a group - process it
                            if group_rows:
                                # Find min price in this group (USING FINAL PRICE)
                                prices = [(r, export_df.iloc[r-2]['Final Price']) for r in group_rows if pd.notna(export_df.iloc[r-2]['Final Price'])]
                                if prices:
                                    min_row = min(prices, key=lambda x: x[1])[0]
                                    
                                    # Highlight ENTIRE ROW
                                    for col_idx in range(1, len(export_df.columns) + 1):
                                        worksheet.cell(row=min_row, column=col_idx).fill = green_fill
                                    
                                group_rows = []
                        else:
                            # Add row to current group
                            group_rows.append(row_idx)
                    
                    # Process last group if exists
                    if group_rows:
                        prices = [(r, export_df.iloc[r-2]['Final Price']) for r in group_rows if pd.notna(export_df.iloc[r-2]['Final Price'])]
                        if prices:
                            min_row = min(prices, key=lambda x: x[1])[0]
                            
                            # Highlight ENTIRE ROW
                            for col_idx in range(1, len(export_df.columns) + 1):
                                worksheet.cell(row=min_row, column=col_idx).fill = green_fill
                    
                    # Auto-size columns
                    for idx, col in enumerate(export_df.columns):
                        max_len = len(str(col)) + 2
                        # Check length of data in column
                        col_max = export_df[col].astype(str).str.len().max()
                        if pd.notna(col_max):
                            max_len = max(max_len, col_max + 2)
                        worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 50)
                else:
                    # Fallback if no comparison data
                    pd.DataFrame(['No comparison data available (check SKU length filter)']).to_excel(writer, index=False, sheet_name='Price Comparison')
                
                # Sheet 2+: Individual Quotation Sheets (One per file)
                # We group by quotation_id to ensure 1 tab per uploaded file
                if 'quotation_id' in df.columns:
                    conn = sqlite3.connect('quotations.db')
                    session_q_ids = df['quotation_id'].unique()
                    
                    for q_id in session_q_ids:
                        if pd.isna(q_id): continue
                        
                        # Get filename for this quotation
                        q_data = pd.read_sql_query("SELECT filename FROM quotations WHERE id = ?", conn, params=(int(q_id),))
                        if not q_data.empty:
                            filename = q_data.iloc[0]['filename']
                            # Clean filename for sheet name (Excel limit 31 chars)
                            base_name = os.path.splitext(filename)[0]
                            sheet_name = base_name[:30]
                            # Remove invalid characters
                            for char in ['[', ']', '*', '?', '/', '\\', ':']:
                                sheet_name = sheet_name.replace(char, '')
                            
                            # Get items for this quotation
                            q_items = df[df['quotation_id'] == q_id].copy()
                            
                            # Select relevant columns
                            sheet_df = q_items[['sku', 'product_name', 'quantity', 'unit_price', 'total_price', 'tax_rate', 'discount_rate']].copy()
                            
                            # Apply Discount and Tax to Price and Final Price
                            def calculate_net_price(row):
                                price = row['unit_price']
                                discount = row.get('discount_rate', 0.0)
                                tax = row.get('tax_rate', 0.0)
                                price_net = price * (1 - discount/100.0) * (1 + tax/100.0)
                                return price_net

                            def calculate_net_total(row):
                                total = row['total_price']
                                discount = row.get('discount_rate', 0.0)
                                tax = row.get('tax_rate', 0.0)
                                total_net = total * (1 - discount/100.0) * (1 + tax/100.0)
                                return total_net

                            sheet_df['unit_price'] = sheet_df.apply(calculate_net_price, axis=1)
                            sheet_df['total_price'] = sheet_df.apply(calculate_net_total, axis=1)
                            
                            # Format Tax and Discount for display
                            sheet_df['tax_rate'] = sheet_df['tax_rate'].apply(lambda x: f"{x}%" if pd.notna(x) else "0%")
                            sheet_df['discount_rate'] = sheet_df['discount_rate'].apply(lambda x: f"{x}%" if pd.notna(x) else "0%")

                            # Rename columns
                            sheet_df = sheet_df[['sku', 'product_name', 'quantity', 'unit_price', 'total_price', 'tax_rate', 'discount_rate']]
                            sheet_df.columns = ['SKU', 'Product Name', 'Quantity', 'Price', 'Final Price', 'Tax Rate', 'Discount Rate']
                            
                            sheet_df.to_excel(writer, index=False, sheet_name=sheet_name)
                            
                            # Add Total Row
                            worksheet = writer.sheets[sheet_name]
                            last_row = len(sheet_df) + 2 # Header + Data + 1
                            
                            # Calculate Total
                            total_sum = sheet_df['Final Price'].sum()
                            
                            # Write Total Label
                            worksheet.cell(row=last_row, column=1, value="TOTAL")
                            
                            # Write Total Value (Final Price is column 5)
                            worksheet.cell(row=last_row, column=5, value=total_sum)
                            
                            # Bold the total row
                            from openpyxl.styles import Font
                            bold_font = Font(bold=True)
                            for col in range(1, 8): # Adjusted for new columns
                                worksheet.cell(row=last_row, column=col).font = bold_font
                            
                            # Auto-size columns
                            worksheet = writer.sheets[sheet_name]
                            for idx, col in enumerate(sheet_df.columns):
                                max_len = len(str(col)) + 2
                                col_max = sheet_df[col].astype(str).str.len().max()
                                if pd.notna(col_max):
                                    max_len = max(max_len, col_max + 2)
                                worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 50)
                    conn.close()
                
                st.success(f"✅ Price Comparison Excel generated")
            
            import time
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            st.download_button(
                f"📥 Download Excel ({timestamp})",
                buffer.getvalue(),
                f"quotation_comparison_{timestamp}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key='download-excel'
            )

else:
    st.info("Upload PDFs to start comparing.")
