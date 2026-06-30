import chromadb
from chromadb.errors import NotFoundError
from openai import OpenAI

from src.config import settings
from src.knowledge_loader import KnowledgeChunk


COLLECTION_NAME = "autocare_knowledge_base"


def _client() -> chromadb.PersistentClient:
    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(settings.chroma_dir))


def _openai_client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key)


def _embed(texts: list[str]) -> list[list[float]]:
    response = _openai_client().embeddings.create(
        model=settings.openai_embedding_model,
        input=texts,
    )
    return [item.embedding for item in response.data]


def build_vector_store(chunks: list[KnowledgeChunk]) -> None:
    client = _client()
    try:
        client.delete_collection(COLLECTION_NAME)
    except (ValueError, NotFoundError):
        pass

    collection = client.create_collection(COLLECTION_NAME)
    if not chunks:
        return

    texts = [chunk.text for chunk in chunks]
    collection.add(
        ids=[f"{chunk.source}:{chunk.chunk_id}" for chunk in chunks],
        documents=texts,
        embeddings=_embed(texts),
        metadatas=[chunk.metadata for chunk in chunks],
    )


def search_relevant_chunks(query: str, limit: int = 4) -> list[dict[str, str]]:
    collection = _client().get_or_create_collection(COLLECTION_NAME)
    results = collection.query(
        query_embeddings=_embed([query]),
        n_results=limit,
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    chunks: list[dict[str, str]] = []
    for text, metadata in zip(documents, metadatas):
        chunks.append(
            {
                "text": text,
                "source": str(metadata.get("source", "")) if metadata else "",
                "title": str(metadata.get("title", "")) if metadata else "",
                "domain": str(metadata.get("domain", "")) if metadata else "",
                "heading": str(metadata.get("heading", "")) if metadata else "",
                "heading_path": str(metadata.get("heading_path", "")) if metadata else "",
            }
        )
    return chunks
