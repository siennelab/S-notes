"""Structured failures exposed by Package 1 application services."""

from __future__ import annotations


class NoteNotFoundError(ValueError):
    def __init__(self, note_id: int, *, deleted_allowed: bool = False) -> None:
        if deleted_allowed:
            message = f"笔记不存在: {note_id}"
        else:
            message = f"笔记不存在或已删除: {note_id}"
        super().__init__(message)


class NoteRevisionConflictError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("这篇笔记已被其他来源修改，请重新加载后处理。")


class DirectoryNotFoundError(ValueError):
    def __init__(self, directory_id: int, *, trash: bool = False) -> None:
        message = (
            f"回收站中找不到目录: {directory_id}"
            if trash
            else f"目录不存在: {directory_id}"
        )
        super().__init__(message)
