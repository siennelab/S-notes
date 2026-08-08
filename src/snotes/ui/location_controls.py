"""UI-only helpers for the approved two-level classification controls."""

from __future__ import annotations

from src.snotes.application.directory_service import DirectoryService
from src.snotes.domain.models import Directory, Note, NoteLocation


def populate_location_controls(shell, note: Note, service: DirectoryService | None) -> None:
    combos = (shell.main_combo, shell.sub_combo)
    for combo in combos:
        combo.blockSignals(True)
    if service is None:
        for combo, value in zip(combos, note.location.values()[:2]):
            fill_combo(combo, [value], value)
    else:
        directories = service.list_directories()
        fill_combo(shell.main_combo, [row.name for row in directories if row.parent_id is None], note.main_category)
        main_id = directory_id_for_path((shell.main_combo.currentText(),), directories, service)
        fill_combo(shell.sub_combo, ["无", *[row.name for row in directories if row.parent_id == main_id and row.name != "无"]], note.sub_category)
    for combo in combos:
        combo.blockSignals(False)


def populate_dependent_location_controls(shell, service: DirectoryService) -> None:
    directories = service.list_directories()
    main_id = directory_id_for_path((shell.main_combo.currentText(),), directories, service)
    current_sub = shell.sub_combo.currentText()
    shell.sub_combo.blockSignals(True)
    fill_combo(shell.sub_combo, ["无", *[row.name for row in directories if row.parent_id == main_id and row.name != "无"]], current_sub)
    shell.sub_combo.blockSignals(False)


def selected_location(shell) -> NoteLocation:
    return NoteLocation(
        shell.main_combo.currentText(),
        shell.sub_combo.currentText(),
        "无",
    )


def location_from_path(path: tuple[str, ...]) -> NoteLocation:
    return NoteLocation(
        path[0],
        path[1] if len(path) >= 2 else "无",
        "无",
    )


def ordered_directories(service: DirectoryService) -> list[Directory]:
    return sorted(
        (
            row
            for row in service.list_directories()
            if len(service.get_directory_path(row.id)) <= 2
        ),
        key=lambda row: (len(service.get_directory_path(row.id)), row.name.casefold(), row.id),
    )


def directory_id_for_path(
    path: tuple[str, ...],
    directories: list[Directory],
    service: DirectoryService,
) -> int | None:
    return next(
        (row.id for row in directories if service.get_directory_path(row.id) == path),
        None,
    )


def fill_combo(combo, values: list[str], selected: str) -> None:
    combo.clear()
    combo.addItems(list(dict.fromkeys(value for value in values if value)))
    index = combo.findText(selected)
    combo.setCurrentIndex(index if index >= 0 else 0)
