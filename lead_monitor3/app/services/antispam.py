import asyncio
import random
from collections import defaultdict, deque
from datetime import datetime, timedelta


class AntiSpam:
    """
    Per-user rate limiter:
    - Allow up to MAX_HITS triggers within WINDOW
    - On exceed: permanent session blacklist
    """
    WINDOW = timedelta(minutes=10)
    MAX_HITS = 2

    def __init__(self) -> None:
        self._hits: dict[int, deque] = defaultdict(deque)
        self._blacklist: set[int] = set()

    def is_allowed(self, user_id: int) -> bool:
        if user_id in self._blacklist:
            return False
        self._evict(user_id)
        return len(self._hits[user_id]) < self.MAX_HITS

    def register(self, user_id: int) -> None:
        self._evict(user_id)
        self._hits[user_id].append(datetime.utcnow())
        if len(self._hits[user_id]) >= self.MAX_HITS:
            self._blacklist.add(user_id)

    def _evict(self, user_id: int) -> None:
        cutoff = datetime.utcnow() - self.WINDOW
        dq = self._hits[user_id]
        while dq and dq[0] < cutoff:
            dq.popleft()

    @staticmethod
    async def human_delay(min_ms: int = 1500, max_ms: int = 4500) -> None:
        await asyncio.sleep(random.randint(min_ms, max_ms) / 1000)
