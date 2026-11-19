"""Turn an uploaded saved web page into plain text, in memory.

The HTML itself is never stored. The resulting text goes through the same parsers as
pasted text. Scripts, styles and hidden elements are dropped; nothing is executed.
"""
from __future__ import annotations

from bs4 import BeautifulSoup, UnicodeDammit

MAX_BYTES = 10 * 1024 * 1024


class UploadTooLarge(ValueError):
    pass


def html_to_text(data: bytes | str) -> str:
    if isinstance(data, bytes):
        if len(data) > MAX_BYTES:
            raise UploadTooLarge("The file is larger than 10 MB.")
        data = UnicodeDammit(data, is_html=True).unicode_markup  # honours <meta charset>
    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template", "head", "iframe"]):
        tag.decompose()
    for tag in soup.find_all(attrs={"hidden": True}):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(l for l in lines if l)
