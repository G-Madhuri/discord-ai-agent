"""Document parsing service for PDF, DOCX, TXT, and MD files."""

from __future__ import annotations

import io
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB limit


def parse_document(file_bytes: bytes, filename_or_ext: str) -> dict[str, Any]:
    """Parse raw bytes of a document (.pdf, .docx, .txt, .md) and extract plain text."""
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise ValueError(f"File size ({len(file_bytes)} bytes) exceeds the 10 MB limit.")

    ext = filename_or_ext.lower()
    if "." in ext:
        ext = "." + ext.rsplit(".", 1)[-1]

    text = ""
    page_count = 1
    warnings: list[str] = []

    try:
        if ext == ".pdf":
            import pypdf

            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            page_count = len(reader.pages)
            extracted_pages = []
            for page in reader.pages:
                page_text = page.extract_text() or ""
                extracted_pages.append(page_text)
            text = "\n".join(extracted_pages).strip()

        elif ext == ".docx":
            import docx

            doc = docx.Document(io.BytesIO(file_bytes))
            paragraphs = [p.text for p in doc.paragraphs if p.text]
            text = "\n".join(paragraphs).strip()
            page_count = 1

        elif ext in (".txt", ".md", ".text", ".markdown"):
            text = file_bytes.decode("utf-8", errors="ignore").strip()
            page_count = 1

        else:
            raise ValueError(f"Unsupported file format '{filename_or_ext}'. Allowed formats: .pdf, .docx, .txt, .md")

    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        logger.warning("Error parsing document %s: %s", filename_or_ext, exc)
        warnings.append(f"Document parsing error: {exc}")

    if len(text) < 50:
        warnings.append("Extracted text is less than 50 characters (unreadable or empty document).")

    return {
        "text": text,
        "page_count": page_count,
        "warnings": warnings,
    }
