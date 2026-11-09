"""Document generation from recorded evidence."""

from aimrg.reports.docx import DocxUnavailableError, markdown_to_docx
from aimrg.reports.render import DISCLAIMER, available_templates, render

__all__ = [
    "DISCLAIMER",
    "DocxUnavailableError",
    "available_templates",
    "markdown_to_docx",
    "render",
]
