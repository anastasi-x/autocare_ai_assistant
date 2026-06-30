import hashlib

from openai import OpenAI

from src.cache import cache
from src.config import settings
from src.dialog_memory import dialog_memory
from src.privacy import should_skip_cache
from src.sheets import load_service_topics
from src.vector_store import search_relevant_chunks


def _system_prompt() -> str:
    return settings.prompt_file.read_text(encoding="utf-8")


def _cache_key(question: str) -> str:
    normalized = " ".join(question.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def answer_question(question: str, user_id: int | None = None) -> str:
    dialog_context = dialog_memory.format_context(user_id)
    cache_source = f"{question}\n{dialog_context}" if dialog_context else question
    use_cache = not should_skip_cache(cache_source)
    key = _cache_key(question)

    if use_cache:
        cached = cache.get(key)
        if cached:
            return cached

    chunks = search_relevant_chunks(question)
    context = "\n\n".join(
        f"Источник: {chunk['source']}\n"
        f"Документ: {chunk.get('title', '')}\n"
        f"Markdown-раздел: {chunk.get('heading_path', '')}\n"
        f"{chunk['text']}"
        for chunk in chunks
    )
    service_topics = load_service_topics()
    topics_context = "\n".join(
        f"- {topic.get('Категория', '')}: {topic.get('Описание', '')}. "
        f"Данные: {topic.get('Какие данные собрать', '')}. "
        f"Передача: {topic.get('Когда передавать специалисту', '')}."
        for topic in service_topics
        if topic.get("Статус", "active") != "inactive"
    )

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": _system_prompt()},
            {
                "role": "user",
                "content": (
                    f"Контекст базы знаний:\n{context or 'Нет найденного контекста.'}\n\n"
                    f"Темы из ServiceTopics:\n{topics_context or 'Нет данных.'}\n\n"
                    f"Последний диалог с клиентом:\n{dialog_context or 'Нет истории.'}\n\n"
                    f"Вопрос клиента:\n{question}"
                ),
            },
        ],
        temperature=0.2,
    )
    answer = response.choices[0].message.content or "Не удалось подготовить ответ."

    if use_cache:
        cache.set(key, answer)

    return answer
