"""Application boundary for GreenCloud Notes proposals.

The service reads only the caller's already-filtered actor mirror and sends
validated proposals through the authenticated localhost outbox client.  It
never opens the outbox database or canonicalizes Markdown.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Protocol

from src.snotes.application.cloud_read_service import CloudMirrorReader
from src.snotes.application.note_canonical_candidate import apply_exact_markdown_edits


ALLOWED_OPERATION_TYPES = frozenset({"create", "append", "edit"})
DISALLOWED_OPERATION_TYPES = frozenset(
    {"prepend", "metadata", "update_note_metadata", "delete"}
)
MAX_REQUEST_ID_LENGTH = 128
MAX_TITLE_LENGTH = 500
MAX_CONTENT_LENGTH = 50_000
MAX_CATEGORY_LENGTH = 120
MAX_TAG_LENGTH = 80
MAX_TAGS = 32
MAX_EDITS = 100
MAX_NOTE_ID = 9_223_372_036_854_775_807
VERSION_PREFIX = "snotes-v1-"
_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`).*?\1(?!`)", re.DOTALL)
_FENCE_START = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_SIZE_DIRECTIVE = re.compile(r'(?<!\\):size\[(?:\\.|[^\]\\\n])*\]\{([^}\n]*)\}')
_VALID_SIZE_ATTRIBUTES = re.compile(r'''\s*size\s*=\s*(?:"(?:15|20)"|'(?:15|20)'|(?:15|20))\s*''')


class CloudProposalGateway(Protocol):
    def get_operation_status(self, request_id: str) -> dict | None: ...

    def enqueue_proposal(
        self,
        *,
        request_id: str,
        operation_type: str,
        note_id: int | None,
        expected_version: str | None,
        payload: dict,
    ) -> dict: ...


class CloudProposalService:
    """Precheck actor-scoped proposals before the localhost queue boundary."""

    def __init__(self, reader: CloudMirrorReader, gateway: CloudProposalGateway) -> None:
        self._reader = reader
        self._gateway = gateway

    def create_note(
        self,
        *,
        request_id: str,
        title: str,
        content: str,
        directory_id: int | None = None,
        main_category: str | None = None,
        sub_category: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        request_id = _request_id(request_id)
        duplicate = self._duplicate(request_id)
        if duplicate is not None:
            return duplicate
        try:
            clean_title = _bounded_text(title, "title", MAX_TITLE_LENGTH)
            clean_content = _bounded_text(content, "content", MAX_CONTENT_LENGTH)
            _validate_markdown(clean_content)
            clean_directory_id = (
                None if directory_id is None else _note_id(directory_id, "directory_id")
            )
            clean_main = _optional_text(main_category, "main_category", MAX_CATEGORY_LENGTH)
            clean_sub = _optional_text(sub_category, "sub_category", MAX_CATEGORY_LENGTH)
            if clean_main is None and clean_sub is not None:
                raise ValueError("main_category_required")
            clean_tags = _tags(tags)
        except (TypeError, ValueError) as error:
            return _rejected(request_id, str(error) or "invalid_request")
        payload = {
            "title": clean_title,
            "content": clean_content,
            "directory_id": clean_directory_id,
            "main_category": clean_main,
            "sub_category": clean_sub,
            "tags": clean_tags,
        }
        return self._enqueue(
            request_id=request_id,
            operation_type="create",
            note_id=None,
            expected_version=None,
            payload=payload,
        )

    def append_note(
        self,
        *,
        request_id: str,
        note_id: int,
        content: str,
        expected_version: str,
    ) -> dict:
        request_id = _request_id(request_id)
        duplicate = self._duplicate(request_id)
        if duplicate is not None:
            return duplicate
        try:
            note_id = _note_id(note_id)
            content = _bounded_text(content, "content", MAX_CONTENT_LENGTH)
            expected_version = _version(expected_version)
            _validate_markdown(content)
            note = self._reader.get(note_id)
        except (TypeError, ValueError):
            return _rejected(request_id, "invalid_request", note_id=_safe_note_id(note_id))
        if note is None:
            return _not_found(request_id, note_id)
        if str(note.get("version", "")) != expected_version:
            return _conflict(request_id, note_id, "stale_expected_version")
        return self._enqueue(
            request_id=request_id,
            operation_type="append",
            note_id=note_id,
            expected_version=expected_version,
            payload={"content": content},
        )

    def edit_note(
        self,
        *,
        request_id: str,
        note_id: int,
        expected_version: str,
        edits: list[dict[str, str]],
    ) -> dict:
        request_id = _request_id(request_id)
        duplicate = self._duplicate(request_id)
        if duplicate is not None:
            return duplicate
        try:
            note_id = _note_id(note_id)
            expected_version = _version(expected_version)
            note = self._reader.get_markdown(note_id)
        except (TypeError, ValueError):
            return _rejected(request_id, "invalid_request", note_id=_safe_note_id(note_id))
        if note is None:
            return _not_found(request_id, note_id)
        if str(note.get("dialect", "")) != "snotes-markdown-v1":
            return _rejected(request_id, "dialect_unavailable", note_id=note_id)
        if str(note.get("version", "")) != expected_version:
            return _conflict(request_id, note_id, "stale_expected_version")
        if not isinstance(edits, list) or len(edits) > MAX_EDITS:
            return _rejected(request_id, "edits_invalid", note_id=note_id)
        try:
            for edit in edits:
                if not isinstance(edit, dict) or set(edit) != {"old_markdown", "new_markdown"}:
                    raise ValueError("edits_invalid")
                _bounded_text(edit["old_markdown"], "old_markdown", MAX_CONTENT_LENGTH)
                new = edit["new_markdown"]
                if not isinstance(new, str) or len(new) > MAX_CONTENT_LENGTH:
                    raise ValueError("new_markdown_invalid")
            # This performs the exact, unique, non-overlapping match check and
            # rejects newly introduced raw HTML outside code/escaped literals.
            before_markdown = str(note["markdown"])
            candidate = apply_exact_markdown_edits(before_markdown, edits)
            if _invalid_size_values(candidate) - _invalid_size_values(before_markdown):
                raise ValueError("unsupported_size_directive")
        except (KeyError, TypeError, ValueError) as error:
            return _rejected(request_id, str(error) or "edits_invalid", note_id=note_id)
        return self._enqueue(
            request_id=request_id,
            operation_type="edit",
            note_id=note_id,
            expected_version=expected_version,
            payload={"edits": edits},
        )

    def get_operation_status(self, request_id: str) -> dict:
        request_id = _request_id(request_id)
        result = self._gateway.get_operation_status(request_id)
        return result if result is not None else {
            "status": "not_found",
            "request_id": request_id,
        }

    def reject_operation(self, operation_type: str, request_id: str = "") -> dict:
        return _rejected(str(request_id or ""), f"operation_not_allowed:{operation_type}")

    def _duplicate(self, request_id: str) -> dict | None:
        existing = self._gateway.get_operation_status(request_id)
        if existing is None:
            return None
        return {**existing, "duplicate": True}

    def _enqueue(
        self,
        *,
        request_id: str,
        operation_type: str,
        note_id: int | None,
        expected_version: str | None,
        payload: dict,
    ) -> dict:
        if operation_type not in ALLOWED_OPERATION_TYPES:
            return self.reject_operation(operation_type, request_id)
        try:
            return self._gateway.enqueue_proposal(
                request_id=request_id,
                operation_type=operation_type,
                note_id=note_id,
                expected_version=expected_version,
                payload=payload,
            )
        except RuntimeError as error:
            reason = str(error) or "outbox_unavailable"
            status = "queue_full" if reason == "queue_full" else "failed"
            return {"status": status, "request_id": request_id, "reason": reason}


def _request_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_REQUEST_ID_LENGTH:
        raise ValueError("request_id_invalid")
    if any(character.isspace() for character in value):
        raise ValueError("request_id_invalid")
    return value


def _note_id(value: object, field: str = "note_id") -> int:
    if type(value) is not int:
        raise ValueError(f"{field}_invalid")
    normalized = value
    if not 1 <= normalized <= MAX_NOTE_ID:
        raise ValueError(f"{field}_invalid")
    return normalized


def _safe_note_id(value: object) -> int | None:
    try:
        return _note_id(value)
    except (TypeError, ValueError):
        return None


def _version(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 74
        or not value.startswith(VERSION_PREFIX)
        or any(character not in "0123456789abcdef" for character in value[10:])
    ):
        raise ValueError("expected_version_invalid")
    return value


def _bounded_text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"{field}_invalid")
    return value


def _optional_text(value: object, field: str, limit: int) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, field, limit)


def _tags(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_TAGS:
        raise ValueError("tags_invalid")
    result: list[str] = []
    for item in value:
        tag = _bounded_text(item, "tags", MAX_TAG_LENGTH).strip()
        if not tag:
            raise ValueError("tags_invalid")
        if tag not in result:
            result.append(tag)
    return result


def _validate_markdown(markdown: str) -> None:
    # Reuse the established raw-HTML dialect check without canonicalizing or
    # changing bytes. An impossible marker body makes this a pure validation.
    marker = "\x00snotes-cloud-validation-marker\x00"
    try:
        apply_exact_markdown_edits(
            f"prefix{marker}suffix",
            [{"old_markdown": marker, "new_markdown": markdown}],
        )
    except ValueError as error:
        raise ValueError(str(error) or "markdown_invalid") from error
    if _invalid_size_values(markdown):
        raise ValueError("unsupported_size_directive")


def _invalid_size_values(markdown: str) -> Counter[str]:
    return Counter(
        attributes
        for attributes in _SIZE_DIRECTIVE.findall(_markdown_outside_code(markdown))
        if _VALID_SIZE_ATTRIBUTES.fullmatch(attributes) is None
    )


def _markdown_outside_code(markdown: str) -> str:
    visible_lines: list[str] = []
    fence_character = ""
    fence_length = 0
    for line in markdown.splitlines(keepends=True):
        fence = _FENCE_START.match(line)
        if fence_character:
            if (
                fence is not None
                and fence.group(1)[0] == fence_character
                and len(fence.group(1)) >= fence_length
                and not line[fence.end():].strip()
            ):
                fence_character = ""
                fence_length = 0
            visible_lines.append("\n")
        elif fence is not None:
            fence_character = fence.group(1)[0]
            fence_length = len(fence.group(1))
            visible_lines.append("\n")
        else:
            visible_lines.append(line)
    return _INLINE_CODE.sub("", "".join(visible_lines))


def _rejected(request_id: str, reason: str, *, note_id: int | None = None) -> dict:
    return {
        "status": "rejected",
        "request_id": request_id,
        "note_id": note_id,
        "reason": reason,
        "changed_formal_note": False,
    }


def _not_found(request_id: str, note_id: int) -> dict:
    return {
        "status": "not_found",
        "request_id": request_id,
        "note_id": note_id,
        "changed_formal_note": False,
    }


def _conflict(request_id: str, note_id: int, reason: str) -> dict:
    return {
        "status": "conflict",
        "request_id": request_id,
        "note_id": note_id,
        "reason": reason,
        "changed_formal_note": False,
    }
