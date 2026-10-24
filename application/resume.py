import re
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError


def extract_resume(content):
    if not content or len(content) > 5 * 1024 * 1024:
        raise ValueError("Upload a resume PDF no larger than 5 MB.")
    if not content.lstrip().startswith(b"%PDF-"):
        raise ValueError("This file is not a valid PDF. Export your resume as a PDF and try again.")
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            raise ValueError("This PDF is password protected. Upload an unencrypted copy.")
        if not 1 <= len(reader.pages) <= 20:
            raise ValueError("Upload a resume containing between 1 and 20 pages.")
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            # Some PDF fonts map a bullet to the DEL control character.
            text = re.sub(r"(?m)^(\s*)\x7f", r"\1•", text)
            if not text.strip():
                raise ValueError(
                    f"No readable text was found on page {number}. "
                    "Scanned or image-only pages need OCR first. Upload a text-based PDF."
                )
            pages.append(text.strip())
        return "\n\n".join(pages)
    except (PyPdfError, OSError, KeyError, IndexError, TypeError) as error:
        raise ValueError("Could not read this PDF. Export a new PDF from your original resume and try again.") from error
