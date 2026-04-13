"""
Markdown AST helpers using markdown-it-py (CommonMark token stream).

Used for deterministic code-block extraction aligned with the parser's view of the document.
"""

from __future__ import annotations

from collections.abc import Iterator

from markdown_it import MarkdownIt
from markdown_it.token import Token


def _walk_tokens(tokens: list[Token]) -> Iterator[Token]:
    for tok in tokens:
        yield tok
        if tok.children:
            yield from _walk_tokens(tok.children)


def fenced_blocks_from_markdown(source: str) -> list[tuple[str, str]]:
    """
    Return (language, code_body) for each `fence` and `code_block` token, in document order.

    Bodies are sliced from the **original** `source` using each token's `map` (line indices),
    so every `code` is guaranteed to be a contiguous substring of `source` (unlike
    `tok.content` for indented `code_block`, which can be normalized / dedented).
    """
    lines = source.splitlines(keepends=True)
    md = MarkdownIt("commonmark")
    tokens = md.parse(source)
    out: list[tuple[str, str]] = []
    for tok in _walk_tokens(tokens):
        if tok.type == "fence":
            lang = (tok.info or "").strip()
            m = tok.map
            if not m or len(m) < 2:
                out.append((lang, tok.content))
                continue
            a, b = int(m[0]), int(m[1])
            block = lines[a:b]
            if len(block) >= 2:
                # Drop opening ```info and closing ``` lines; keep inner verbatim.
                body = "".join(block[1:-1])
            else:
                body = tok.content
            out.append((lang, body))
        elif tok.type == "code_block":
            m = tok.map
            if not m or len(m) < 2:
                out.append(("", tok.content))
                continue
            a, b = int(m[0]), int(m[1])
            body = "".join(lines[a:b])
            out.append(("", body))
    return out
