"""Pydantic schemas shared by distill.py and merge.py."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CodeSnippet(BaseModel):
    """One fenced or indented code block; body must match source verbatim."""

    model_config = ConfigDict(extra="forbid")

    language: str = Field(
        description="Language tag from opening fence (e.g. python, ts). Empty string if none / indented block.",
    )
    code: str = Field(
        description="Exact characters inside the fence or code block; no reformatting or truncation.",
    )


class RejectedIdea(BaseModel):
    """An approach that was tried and failed or explicitly rejected."""

    model_config = ConfigDict(extra="forbid")

    idea: str = Field(description="What was tried or proposed, as stated in the log.")
    reason: str = Field(
        description="Concrete reason: error text, log quote, or explicit rejection wording.",
    )


class ChunkExtraction(BaseModel):
    """Strict factual extraction for one chat-log chunk."""

    model_config = ConfigDict(extra="forbid")

    entities: list[str] = Field(
        description="Technical names, libraries, versions, error codes appearing in the chunk.",
    )
    context: str | None = Field(
        description=(
            "Single string composed ONLY of substrings copied verbatim from the input (concatenate short quotes if needed). "
            "No paraphrase. null if nothing to cite."
        ),
    )
    decisions: list[str] = Field(
        description="Confirmed specs or adopted approaches; only from explicit statements, not inference.",
    )
    rejected_ideas: list[RejectedIdea] = Field(
        description="Failed attempts or rejected ideas with concrete reasons from the log.",
    )
    code_snippets: list[CodeSnippet] = Field(
        description="All fence and indented code_block nodes in markdown-it document order; bodies match parser output.",
    )


def extraction_json_schema() -> dict:
    return ChunkExtraction.model_json_schema(mode="serialization")


class MergedExtraction(BaseModel):
    """Result of merging multiple ChunkExtraction JSON objects (see merge.py)."""

    model_config = ConfigDict(extra="forbid")

    chunk_count: int = Field(description="Number of chunk extractions merged.")
    entities: list[str] = Field(description="De-duplicated entities in first-seen order across chunks.")
    contexts: list[str | None] = Field(
        description="Per-chunk context strings in merge order (same length as chunk_count).",
    )
    decisions: list[str] = Field(description="All decisions in chunk order, lightly de-duplicated (adjacent dup removed).")
    rejected_ideas: list[RejectedIdea] = Field(description="All rejected_ideas in chunk order.")
    code_snippets: list[CodeSnippet] = Field(description="All code_snippets in chunk order.")
