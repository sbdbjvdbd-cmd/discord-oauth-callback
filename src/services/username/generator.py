"""
TikTok Username Generator & 4L Finder
"""
import random
import string
import asyncio
import logging
from typing import AsyncGenerator

from .checker import check_username, STATUS_AVAILABLE, is_valid_username, is_valid_4l

logger = logging.getLogger(__name__)

LETTERS    = string.ascii_lowercase
NUMBERS    = string.digits
UNDERSCORE = "_"


def generate_usernames(
    length: int = 4,
    use_letters: bool = True,
    use_numbers: bool = True,
    use_underscore: bool = False,
    prefix: str = "",
    suffix: str = "",
    count: int = 10,
) -> list[str]:
    """Generiert zufällige Usernames nach den angegebenen Parametern."""
    charset = ""
    if use_letters:
        charset += LETTERS
    if use_numbers:
        charset += NUMBERS
    if use_underscore:
        charset += UNDERSCORE
    if not charset:
        charset = LETTERS

    results = set()
    attempts = 0
    max_attempts = count * 50

    while len(results) < count and attempts < max_attempts:
        attempts += 1
        remaining = length - len(prefix) - len(suffix)
        if remaining <= 0:
            break
        middle = "".join(random.choices(charset, k=remaining))
        username = prefix + middle + suffix
        if is_valid_username(username):
            results.add(username)

    return list(results)[:count]


async def find_available_4l(
    use_letters: bool = True,
    use_numbers: bool = True,
    use_underscore: bool = False,
    max_checks: int = 30,
    delay: float = 0.8,
) -> AsyncGenerator[dict, None]:
    """
    Generator der 4L-Usernames generiert und prüft.
    Gibt für jeden Check ein Dict zurück: {"username": str, "status": str, ...}
    """
    checked = 0
    tried   = set()

    charset = ""
    if use_letters:
        charset += LETTERS
    if use_numbers:
        charset += NUMBERS
    if use_underscore:
        charset += UNDERSCORE
    if not charset:
        charset = LETTERS

    while checked < max_checks:
        # Zufällige 4L-Kombination
        for _ in range(200):
            candidate = "".join(random.choices(charset, k=4))
            if candidate not in tried and is_valid_4l(candidate):
                tried.add(candidate)
                break
        else:
            break  # Keine neue Kombination gefunden

        result = await check_username(candidate)
        checked += 1
        yield result

        if checked < max_checks:
            await asyncio.sleep(delay)
