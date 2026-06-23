from __future__ import annotations

from typing import Optional


def _split_tags(tags: Optional[str]) -> list:
    if not tags:
        return []
    return [tag.strip() for tag in tags.split(",") if tag.strip()]


def extract_ast_tags(tags: Optional[str]) -> list:
    return [tag for tag in _split_tags(tags) if tag == "ast" or tag.startswith("ast:")]


def is_ast_tag(tag: str) -> bool:
    normalized = tag.strip()
    return normalized == "ast" or normalized.startswith("ast:")


def build_bt_ast_tags(seed_box_name: str, home_dl_name: str, origin_hash: str) -> str:
    tags = [
        "ast",
        f"ast:origin:{origin_hash}",
        f"ast:route:{seed_box_name}->{home_dl_name}",
    ]
    return ",".join(tag for tag in tags if not tag.endswith(":"))


def build_origin_ast_tags(seed_box_name: str, home_dl_name: str, origin_hash: str, bt_hash: str) -> str:
    tags = [
        "ast",
        f"ast:origin:{origin_hash}",
        f"ast:route:{seed_box_name}->{home_dl_name}",
    ]
    return ",".join(tag for tag in tags if not tag.endswith(":"))


def merge_tags(*tag_groups: Optional[str]) -> Optional[str]:
    merged = []
    seen = set()
    for tag_group in tag_groups:
        for tag in _split_tags(tag_group):
            if tag in seen:
                continue
            seen.add(tag)
            merged.append(tag)
    return ",".join(merged) if merged else None
