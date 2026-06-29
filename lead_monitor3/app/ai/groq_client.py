import logging
from openai import AsyncOpenAI
from app.core.config import settings

log = logging.getLogger(__name__)


class GroqClient:
    _instance: "GroqClient | None" = None

    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
        )

    @classmethod
    def get(cls) -> "GroqClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def complete(self, system: str, user: str, max_tokens: int = 200) -> str:
        if not settings.groq_api_key:
            log.warning("GROQ_API_KEY not set — AI disabled")
            return ""
        try:
            resp = await self._client.chat.completions.create(
                model=settings.groq_model,
                temperature=0.75,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                timeout=25,
            )
            result = (resp.choices[0].message.content or "").strip()
            log.debug("AI response (%d chars): %s", len(result), result[:80])
            return result
        except Exception as e:
            log.error("Groq API error: %s", e)
            return ""
