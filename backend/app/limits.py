import time
from collections import defaultdict, deque
from collections.abc import Callable

HOUR = 3600
DAY = 24 * HOUR


class ModelCallLimiter:
    """Caps calls that spend model tokens, per visitor and in total, for a public demo.

    In memory: a restart resets it, which is fine for one small instance.
    """

    def __init__(
        self, per_hour: int, per_day: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.per_hour = per_hour
        self.per_day = per_day
        self.clock = clock
        self.by_client: defaultdict[str, deque[float]] = defaultdict(deque)
        self.everyone: deque[float] = deque()

    def spend(self, client: str) -> str | None:
        """Records one call, or returns why it is refused."""
        now = self.clock()
        mine = self.by_client[client]
        for calls, window in ((mine, HOUR), (self.everyone, DAY)):
            while calls and now - calls[0] >= window:
                calls.popleft()
        if len(self.everyone) >= self.per_day:
            return "The demo has used its AI budget for today. Please try again tomorrow."
        if len(mine) >= self.per_hour:
            return "You've reached this demo's hourly limit. Please try again in an hour."
        mine.append(now)
        self.everyone.append(now)
        return None
