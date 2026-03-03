"""
Database layer for Cotización Rapida.
SQLite-backed persistence for quotations and extracted items.
"""

import sqlite3
import pandas as pd
import streamlit as st


DB_PATH = 'quotations.db'


def get_connection():
    """Get a SQLite connection."""
    return sqlite3.connect(DB_PATH)


def init_db():
    """Initialize the database schema, adding missing columns if needed."""
    conn = get_connection()
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS quotations 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  filename TEXT, 
                  upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Ensure schema is up to date
    c.execute("PRAGMA table_info(quotations)")
    columns = [info[1] for info in c.fetchall()]
    
    if 'currency' not in columns:
        c.execute("ALTER TABLE quotations ADD COLUMN currency TEXT DEFAULT 'DOP'")
    if 'tax_rate' not in columns:
        c.execute("ALTER TABLE quotations ADD COLUMN tax_rate REAL DEFAULT 0.0")
    if 'discount_rate' not in columns:
        c.execute("ALTER TABLE quotations ADD COLUMN discount_rate REAL DEFAULT 0.0")
    if 'supplier_detected' not in columns:
        c.execute("ALTER TABLE quotations ADD COLUMN supplier_detected TEXT DEFAULT 'Unknown'")
    if 'extractor_used' not in columns:
        c.execute("ALTER TABLE quotations ADD COLUMN extractor_used TEXT DEFAULT 'manual'")
    if 'detection_confidence' not in columns:
        c.execute("ALTER TABLE quotations ADD COLUMN detection_confidence REAL DEFAULT 0.0")
    if 'verification_status' not in columns:
        c.execute("ALTER TABLE quotations ADD COLUMN verification_status TEXT DEFAULT 'pending'")

    c.execute('''CREATE TABLE IF NOT EXISTS items 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  quotation_id INTEGER, 
                  supplier_name TEXT, 
                  product_name TEXT, 
                  sku TEXT, 
                  quantity REAL, 
                  unit_price REAL, 
                  total_price REAL,
                  equipment TEXT,
                  FOREIGN KEY (quotation_id) REFERENCES quotations(id))''')
    
    # Add equipment column if missing
    c.execute("PRAGMA table_info(items)")
    item_columns = [info[1] for info in c.fetchall()]
    if 'equipment' not in item_columns:
        c.execute("ALTER TABLE items ADD COLUMN equipment TEXT")
    
    conn.commit()
    conn.close()


def save_to_db(filename, items, currency='DOP', tax_rate=0.0, discount_rate=0.0,
               supplier_detected='Unknown', extractor_used='manual', 
               detection_confidence=0.0, verification_status='pending'):
    """
    Save a quotation and its items to the database.
    Returns the quotation ID.
    """
    conn = get_connection()
    c = conn.cursor()
    
    c.execute(
        """INSERT INTO quotations 
           (filename, currency, tax_rate, discount_rate, supplier_detected, 
            extractor_used, detection_confidence, verification_status) 
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (filename, currency, tax_rate, discount_rate, supplier_detected,
         extractor_used, detection_confidence, verification_status)
    )
    quotation_id = c.lastrowid
    
    for item in items:
        qty = item.get('quantity') or 0
        price = item.get('unit_price') or 0
        total = item.get('total_price') or (qty * price)
        product_id = item.get('product_id') or item.get('sku')
        equipment = item.get('equipment', '')
        
        c.execute(
            """INSERT INTO items 
               (quotation_id, supplier_name, product_name, sku, quantity, 
                unit_price, total_price, equipment) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (quotation_id, 
             item.get('supplier_name', 'Unknown'),
             item.get('product_name', 'Unknown'),
             product_id, qty, price, total, equipment)
        )
    
    conn.commit()
    conn.close()
    return quotation_id


def get_items_by_quotation_id(quotation_id):
    """Fetch all items for a specific quotation ID, returns list of dicts with metadata."""
    conn = get_connection()
    try:
        items_df = pd.read_sql_query(
            "SELECT * FROM items WHERE quotation_id = ?", 
            conn, params=(quotation_id,)
        )
        
        q_data = pd.read_sql_query(
            """SELECT currency, tax_rate, discount_rate, supplier_detected, 
                      extractor_used, detection_confidence, verification_status 
               FROM quotations WHERE id = ?""",
            conn, params=(quotation_id,)
        )
        
        # Defaults
        metadata = {
            'currency': 'DOP', 'tax_rate': 0.0, 'discount_rate': 0.0,
            'supplier_detected': 'Unknown', 'extractor_used': 'manual',
            'detection_confidence': 0.0, 'verification_status': 'pending'
        }
        
        if not q_data.empty:
            for key in metadata:
                if key in q_data.columns and pd.notna(q_data.iloc[0][key]):
                    metadata[key] = q_data.iloc[0][key]
        
        items_list = items_df.to_dict('records')
        for item in items_list:
            item.update(metadata)
        
        return items_list
    except Exception as e:
        st.error(f"Error fetching items: {e}")
        return []
    finally:
        conn.close()


def get_latest_quotation_items():
    """Get items from the most recent quotation."""
    conn = get_connection()
    try:
        latest_q = pd.read_sql_query(
            "SELECT id FROM quotations ORDER BY upload_date DESC LIMIT 1", conn
        )
        if not latest_q.empty:
            q_id = latest_q.iloc[0]['id']
            return pd.read_sql_query(
                "SELECT * FROM items WHERE quotation_id = ?", 
                conn, params=(int(q_id),)
            )
        return pd.DataFrame()
    except Exception as e:
        st.error(f"Database error: {e}")
        return pd.DataFrame()
    finally:
        conn.close()


def update_items_batch(edited_df):
    """Update multiple items in the database from an edited DataFrame."""
    try:
        conn = get_connection()
        c = conn.cursor()
        
        for _, row in edited_df.iterrows():
            if 'id' in row and pd.notna(row['id']):
                c.execute(
                    """UPDATE items 
                       SET supplier_name=?, product_name=?, sku=?, 
                           quantity=?, unit_price=?, total_price=?
                       WHERE id=?""",
                    (row['supplier_name'], row['product_name'], row['sku'],
                     row['quantity'], row['unit_price'], row['total_price'], row['id'])
                )
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Error updating database: {e}")
        return False


def delete_items_by_ids(item_ids):
    """Delete items from the database by their IDs."""
    if not item_ids:
        return 0
    conn = get_connection()
    c = conn.cursor()
    for del_id in item_ids:
        c.execute("DELETE FROM items WHERE id = ?", (int(del_id),))
    conn.commit()
    conn.close()
    return len(item_ids)


def get_quotation_ids_for_filename(filename):
    """Get all quotation IDs for a given filename."""
    conn = get_connection()
    q_ids = pd.read_sql_query(
        "SELECT id FROM quotations WHERE filename = ?", 
        conn, params=(filename,)
    )
    conn.close()
    return q_ids['id'].tolist() if not q_ids.empty else []


def get_quotation_metadata(quotation_id):
    """Get metadata for a quotation (filename, currency, etc.)."""
    conn = get_connection()
    q_data = pd.read_sql_query(
        "SELECT * FROM quotations WHERE id = ?", 
        conn, params=(int(quotation_id),)
    )
    conn.close()
    return q_data.iloc[0].to_dict() if not q_data.empty else {}
