"""Rule-based filtering: keyword pre-filter and visa/sponsorship phrase rules.

These run before the (paid) AI scoring step to keep API costs down and to
hard-reject postings that explicitly rule out visa sponsorship.
"""


def count_keyword_hits(title: str, description: str, keywords: list[str]) -> int:
    """Count how many configured keyword phrases appear in the title or description."""
    haystack = f"{title}\n{description}".lower()
    return sum(1 for kw in keywords if kw.lower() in haystack)


def rule_reject_reason(description: str, title: str, reject_phrases: list[str]) -> str | None:
    """Return the first matched reject phrase, or None if the posting passes."""
    haystack = f"{title}\n{description}".lower()
    for phrase in reject_phrases:
        if phrase.lower() in haystack:
            return phrase
    return None


def has_positive_sponsorship_phrase(description: str, title: str, positive_phrases: list[str]) -> bool:
    haystack = f"{title}\n{description}".lower()
    return any(phrase.lower() in haystack for phrase in positive_phrases)
