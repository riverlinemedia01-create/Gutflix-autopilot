from .base import Context, Publisher, PublishError, Result
from .instagram import Instagram
from .x import X
from .youtube import YouTube

PUBLISHERS: dict[str, Publisher] = {"youtube": YouTube(), "x": X(), "instagram": Instagram()}

__all__ = ["PUBLISHERS", "Context", "Publisher", "PublishError", "Result"]
