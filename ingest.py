from src.knowledge_loader import load_documents
from src.vector_store import build_vector_store


def main() -> None:
    chunks = load_documents()
    build_vector_store(chunks)
    print(f"Indexed {len(chunks)} knowledge chunks.")


if __name__ == "__main__":
    main()
