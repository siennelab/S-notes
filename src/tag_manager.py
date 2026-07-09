import re


TAG_SPLIT_RE = re.compile(r"[,\s，、]+")


def normalize_tag(tag: str) -> str:
    return tag.strip().lstrip("#").lower()


def parse_tags(text: str) -> list[str]:
    tags = []
    seen = set()
    for raw_tag in TAG_SPLIT_RE.split(text):
        tag = normalize_tag(raw_tag)
        if tag and tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return tags


def format_tags(tags: list[str]) -> str:
    return ", ".join(tags)
