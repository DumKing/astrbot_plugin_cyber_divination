"""Route bare keywords to the existing command handlers."""
from __future__ import annotations

DEFAULT_ALIASES = {
    "zhouyi": ("六爻", "周易", "摇卦"),
    "lingqian": ("求灵签", "观音灵签", "灵签"),
    "tarot": ("塔罗占卜", "塔罗牌", "塔罗"),
    "qimen": ("奇门遁甲", "奇门"),
    "daily": ("每日运势", "今日运势"),
    "help": ("占卜帮助",),
}


def match_keyword(text: str, mode: str, aliases: dict) -> tuple[str, str] | None:
    text = text.strip()
    if not text or text.startswith(("/", "／", "!", "！", "@", "＠", "[CQ:")):
        return None

    if not isinstance(aliases, dict):
        aliases = {}
    routes = []
    for action, defaults in DEFAULT_ALIASES.items():
        words = aliases.get(action, defaults)
        if isinstance(words, (list, tuple)):
            routes.extend(
                (word.strip(), action)
                for word in words
                if isinstance(word, str) and word.strip()
            )
    for word, action in sorted(routes, key=lambda route: -len(route[0])):
        if text == word:
            return action, ""
        if mode == "starts_with":
            index = 0 if text.startswith(word) else -1
        elif mode == "contains":
            index = text.find(word)
        else:
            continue
        if index >= 0:
            return action, text[index + len(word):].lstrip(" \t:：,，")
    return None
