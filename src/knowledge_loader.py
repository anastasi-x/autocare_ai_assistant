import json
import re
from dataclasses import dataclass
from typing import Any

from src.config import settings


@dataclass(frozen=True)
class KnowledgeChunk:
    source: str
    chunk_id: int
    text: str
    metadata: dict[str, Any]


DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 150
METADATA_FIELDS = (
    "title",
    "domain",
    "doc_type",
    "audience",
    "owner",
    "priority",
    "status",
    "language",
)


@dataclass(frozen=True)
class MarkdownSection:
    heading: str
    heading_path: str
    text: str


def load_metadata() -> dict[str, dict[str, Any]]:
    if not settings.metadata_file.exists():
        return {}

    raw_metadata = json.loads(settings.metadata_file.read_text(encoding="utf-8"))
    documents = raw_metadata.get("documents", [])

    if isinstance(documents, list):
        return {
            item["file"]: item
            for item in documents
            if isinstance(item, dict) and isinstance(item.get("file"), str)
        }

    # Backward compatibility for the first simple metadata shape.
    return {
        file_name: item
        for file_name, item in raw_metadata.items()
        if isinstance(item, dict)
    }


def split_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized_text:
        return []

    paragraphs = _split_into_paragraphs(normalized_text)
    if not paragraphs:
        return []

    chunks: list[str] = []
    current_chunk = ""

    for paragraph in paragraphs:
        if len(paragraph) > chunk_size:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""

            chunks.extend(_split_long_text(paragraph, chunk_size, chunk_overlap))
            continue

        candidate = (
            f"{current_chunk}\n\n{paragraph}".strip()
            if current_chunk
            else paragraph
        )
        if len(candidate) <= chunk_size:
            current_chunk = candidate
            continue

        if current_chunk:
            chunks.append(current_chunk.strip())

        overlap_text = _get_overlap_text(current_chunk, chunk_overlap)
        current_chunk = f"{overlap_text}\n\n{paragraph}".strip() if overlap_text else paragraph

    if current_chunk:
        chunks.append(current_chunk.strip())

    return _clean_chunks(chunks)


def chunk_text(
    text: str,
    max_chars: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    return split_text(text, chunk_size=max_chars, chunk_overlap=overlap)


def _split_markdown(text: str) -> list[tuple[str, str, str]]:
    chunks: list[tuple[str, str, str]] = []

    for section in _split_markdown_sections(text):
        prefix = _heading_prefix(section)
        section_chunks = chunk_text(section.text)
        if not section_chunks and prefix:
            section_chunks = [prefix]

        for chunk in section_chunks:
            chunk_with_heading = (
                f"{prefix}\n\n{chunk}".strip()
                if prefix and not chunk.startswith(prefix)
                else chunk
            )
            chunks.append((chunk_with_heading, section.heading, section.heading_path))

    return chunks


def _split_markdown_sections(text: str) -> list[MarkdownSection]:
    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized_text:
        return []

    sections: list[MarkdownSection] = []
    heading_stack: dict[int, str] = {}
    current_lines: list[str] = []
    current_heading = ""
    current_heading_path = ""

    def flush() -> None:
        nonlocal current_lines, current_heading, current_heading_path
        body = "\n".join(current_lines).strip()
        if body or current_heading:
            sections.append(
                MarkdownSection(
                    heading=current_heading,
                    heading_path=current_heading_path,
                    text=body,
                )
            )
        current_lines = []

    for line in normalized_text.split("\n"):
        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading_match:
            flush()
            level = len(heading_match.group(1))
            heading = heading_match.group(2).strip()
            heading_stack[level] = heading
            for stale_level in [key for key in heading_stack if key > level]:
                del heading_stack[stale_level]
            current_heading = heading
            current_heading_path = " > ".join(
                heading_stack[key]
                for key in sorted(heading_stack)
                if key <= level
            )
            continue

        current_lines.append(line)

    flush()
    return sections


def _heading_prefix(section: MarkdownSection) -> str:
    if not section.heading_path:
        return ""
    return f"Раздел: {section.heading_path}"


def _split_into_paragraphs(text: str) -> list[str]:
    raw_paragraphs = re.split(r"\n\s*\n", text.strip())
    paragraphs: list[str] = []

    for raw_paragraph in raw_paragraphs:
        lines = [
            line.strip()
            for line in raw_paragraph.split("\n")
            if line.strip()
        ]
        paragraph = " ".join(lines).strip()
        if paragraph:
            paragraphs.append(paragraph)

    return paragraphs


def _split_long_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    sentences = _split_into_sentences(text)
    chunks: list[str] = []
    current_chunk = ""

    for sentence in sentences:
        if len(sentence) > chunk_size:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""

            chunks.extend(_split_very_long_sentence(sentence, chunk_size, chunk_overlap))
            continue

        candidate = f"{current_chunk} {sentence}".strip() if current_chunk else sentence
        if len(candidate) <= chunk_size:
            current_chunk = candidate
            continue

        if current_chunk:
            chunks.append(current_chunk.strip())

        overlap_text = _get_overlap_text(current_chunk, chunk_overlap)
        current_chunk = f"{overlap_text} {sentence}".strip() if overlap_text else sentence

    if current_chunk:
        chunks.append(current_chunk.strip())

    return _clean_chunks(chunks)


def _split_into_sentences(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def _split_very_long_sentence(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = max(end - chunk_overlap, start + 1)

    return chunks


def _get_overlap_text(text: str, overlap_size: int) -> str:
    if overlap_size <= 0 or not text:
        return ""

    text = text.strip()
    if len(text) <= overlap_size:
        return text

    overlap_candidate = text[-overlap_size:]
    delimiters = [". ", "! ", "? ", "\n"]
    best_position = -1

    for delimiter in delimiters:
        position = overlap_candidate.rfind(delimiter)
        if position > best_position:
            best_position = position + len(delimiter)

    if best_position > 0:
        return overlap_candidate[best_position:].strip()

    return overlap_candidate.strip()


def _clean_chunks(chunks: list[str]) -> list[str]:
    cleaned_chunks: list[str] = []

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        if len(chunk) < 50 and cleaned_chunks:
            cleaned_chunks[-1] = f"{cleaned_chunks[-1]}\n\n{chunk}".strip()
        else:
            cleaned_chunks.append(chunk)

    return cleaned_chunks


def _chunk_metadata(
    source: str,
    chunk_id: int,
    document_metadata: dict[str, Any],
    heading: str = "",
    heading_path: str = "",
) -> dict[str, Any]:
    metadata = {
        "source": source,
        "chunk_id": chunk_id,
    }
    if heading:
        metadata["heading"] = heading
    if heading_path:
        metadata["heading_path"] = heading_path

    metadata.update(
        {
            field: document_metadata[field]
            for field in METADATA_FIELDS
            if field in document_metadata
        }
    )
    return metadata


def load_documents() -> list[KnowledgeChunk]:
    metadata_by_file = load_metadata()
    chunks: list[KnowledgeChunk] = []

    # Only Markdown files are indexed. The JSON manifest is read separately above.
    for path in sorted(settings.knowledge_base_dir.glob("*.md")):
        document_metadata = metadata_by_file.get(path.name, {})
        if document_metadata.get("index") is False:
            continue

        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue

        for index, (chunk_text, heading, heading_path) in enumerate(
            _split_markdown(text),
            start=1,
        ):
            chunks.append(
                KnowledgeChunk(
                    source=path.name,
                    chunk_id=index,
                    text=chunk_text,
                    metadata=_chunk_metadata(
                        path.name,
                        index,
                        document_metadata,
                        heading=heading,
                        heading_path=heading_path,
                    ),
                )
            )

    return chunks
