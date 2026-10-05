"""Build the small discovery catalog carried by GreenCloud actor mirrors."""

from __future__ import annotations

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.tag_service import TagService


class ActorMetadataCatalogService:
    """Read discovery options from the same formal services used on the Mac."""

    def __init__(
        self,
        *,
        directories: DirectoryService,
        tags: TagService,
        ai_metadata: AiAccessMetadataService,
    ) -> None:
        self._directories = directories
        self._tags = tags
        self._ai_metadata = ai_metadata

    def catalog(self) -> dict[str, list]:
        directories: list[dict] = []
        for directory in self._directories.list_directories():
            path = self._directories.get_directory_path(directory.id)
            directories.append(
                {
                    "directory_id": directory.id,
                    "name": directory.name,
                    "parent_id": directory.parent_id,
                    "main_category": path[0] if path else directory.name,
                    "sub_category": path[1] if len(path) > 1 else "",
                    "path": list(path),
                }
            )
        return {
            "directories": sorted(
                directories,
                key=lambda item: (tuple(item["path"]), item["directory_id"]),
            ),
            "tags": sorted(self._tags.list_tags(), key=str.casefold),
            "visibility_options": [
                {"value": value, "label": label}
                for value, label in self._ai_metadata.available_visibility_options()
            ],
            "ai_targets": [
                {
                    "target_id": target.target_id,
                    "display_name": target.display_name,
                }
                for target in self._ai_metadata.available_targets()
            ],
        }
