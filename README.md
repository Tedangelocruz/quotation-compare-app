# Quotation Compare Tool

A Streamlit application to compare price quotations from PDF files.

## Features

*   **PDF Parsing**: Extracts line items from PDF quotations using heuristic parsing or Google Gemini AI.
*   **Price Comparison**: Automatically groups similar products (using fuzzy SKU matching) and compares prices across suppliers.
*   **Excel Export**: Generates a detailed Excel report with:
    *   **Price Comparison Sheet**: Vertical layout with blank rows between SKU groups for easy reading.
    *   **Individual Sheets**: Separate sheets for each supplier's quotation.
*   **Interactive UI**: Edit extracted data, view comparisons, and highlight best prices directly in the browser.

## Setup

1.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

2.  Run the application:
    ```bash
    streamlit run streamlit_app.py
    ```

## Usage

1.  **Upload PDFs**: Drag and drop your quotation PDF files into the sidebar uploader.
2.  **Process**: Click "Process New Files" to extract data.
3.  **Review**: Check the "Combined Quotations" table. You can edit values if needed and click "Save Changes".
4.  **Compare**: Scroll down to the "Price Comparison" section to see the grouped comparison.
5.  **Download**: Use the "Download Excel" button to get the full report.

## Configuration

*   **Gemini API**: Enter your Google Gemini API key in the sidebar settings for enhanced AI-powered extraction.
*   **Theme**: The app uses a custom dark/green theme defined in `.streamlit/config.toml`.
