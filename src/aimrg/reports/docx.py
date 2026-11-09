"""Convert the Markdown that AI-MRG templates produce into a Word document.

Supports the subset the templates use: ATX headings, paragraphs, ``**bold**`` and
`` `code` `` inline spans, bullet lists, block quotes, horizontal rules and pipe tables.
Requires the ``docx`` extra: ``pip install "ai-mrg[docx]"``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`)")
_TABLE_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")


class DocxUnavailableError(RuntimeError):
    pass


def _add_inline(paragraph: Any, text: str) -> None:
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("`") and part.endswith("`"):
            run = paragraph.add_run(part[1:-1])
            run.font.name = "Consolas"
        else:
            paragraph.add_run(part.replace("\\|", "|"))


def _split_row(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    cells = re.split(r"(?<!\\)\|", stripped)
    return [c.strip().replace("\\|", "|") for c in cells]


def markdown_to_docx(markdown: str, path: str | Path, title: str | None = None) -> Path:
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise DocxUnavailableError('Word output needs: pip install "ai-mrg[docx]"') from exc

    document = Document()
    if title:
        document.core_properties.title = title
    lines = markdown.splitlines()
    i = 0
    paragraph_buffer: list[str] = []

    def flush() -> None:
        if paragraph_buffer:
            _add_inline(document.add_paragraph(), " ".join(paragraph_buffer))
            paragraph_buffer.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush()
        elif stripped.startswith("#"):
            flush()
            level = len(stripped) - len(stripped.lstrip("#"))
            document.add_heading(stripped[level:].strip(), level=min(level, 4) - 1 or 0)
        elif stripped in ("---", "***"):
            flush()
            document.add_paragraph("_" * 40)
        elif stripped.startswith(">"):
            flush()
            quote = document.add_paragraph(style="Intense Quote")
            _add_inline(quote, stripped.lstrip("> ").strip())
        elif stripped.startswith(("- ", "* ")):
            flush()
            _add_inline(document.add_paragraph(style="List Bullet"), stripped[2:])
        elif (
            stripped.startswith("|")
            and i + 1 < len(lines)
            and _TABLE_SEPARATOR.match(lines[i + 1].strip())
        ):
            flush()
            header = _split_row(stripped)
            body: list[list[str]] = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append(_split_row(lines[i]))
                i += 1
            table = document.add_table(rows=1 + len(body), cols=len(header))
            table.style = "Light Grid Accent 1"
            for col, text in enumerate(header):
                cell = table.rows[0].cells[col]
                cell.text = ""
                _add_inline(cell.paragraphs[0], f"**{text}**" if text else "")
            for r, row in enumerate(body, start=1):
                for col in range(len(header)):
                    cell = table.rows[r].cells[col]
                    cell.text = ""
                    _add_inline(cell.paragraphs[0], row[col] if col < len(row) else "")
            continue
        else:
            paragraph_buffer.append(stripped)
        i += 1
    flush()

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(out))
    return out
