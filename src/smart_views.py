from src.database import (
    list_ai_visible_notes,
    list_pinned_notes,
    list_recent_notes,
    list_todo_notes,
)


SMART_VIEWS = (
    ("最近", "recent"),
    ("待办", "todo"),
    ("AI资料", "ai"),
)


def get_smart_view_notes(
    view_name: str,
    *,
    actor_target_id: str | None = None,
    query: str = "",
) -> tuple[str, list[dict]]:
    if view_name == "pinned":
        return "固定", list_pinned_notes()
    if view_name == "recent":
        return "最近", list_recent_notes(10)
    if view_name == "todo":
        return "待办", list_todo_notes()
    if view_name == "ai":
        return "AI资料", list_ai_visible_notes(
            actor_target_id=actor_target_id,
            query=query,
        )
    return "目录", []
