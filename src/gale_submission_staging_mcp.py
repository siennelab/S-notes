from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Annotated, Literal

import uvicorn
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.services.gale_submission_outbox import (
    MAX_CONTENT_LENGTH,
    MAX_DIRECTORY_LENGTH,
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_TAG_COUNT,
    MAX_TAG_LENGTH,
    MAX_TITLE_LENGTH,
    GaleSubmissionOutboxError,
    create_submission,
    initialize_outbox,
    outbox_status,
)


LOGGER = logging.getLogger("snotes_gale_r1b_staging")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8878
DEFAULT_OUTBOX_ROOT = Path("/var/lib/snotes-gale-r1b-staging")
TitleText = Annotated[str, Field(min_length=1, max_length=MAX_TITLE_LENGTH)]
ContentText = Annotated[str, Field(min_length=1, max_length=MAX_CONTENT_LENGTH)]
DirectoryText = Annotated[str, Field(max_length=MAX_DIRECTORY_LENGTH)]
TagText = Annotated[str, Field(min_length=1, max_length=MAX_TAG_LENGTH)]
IdempotencyText = Annotated[
    str,
    Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH),
]


class GaleSubmissionStagingError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSubmissionResponse(StrictModel):
    status: Literal["ok", "failed"]
    submission_uuid: str = ""
    external_submission_id: str = ""
    delivery_state: str = ""
    duplicate: bool = False
    reason: str = ""


def load_staging_config(
    environment: dict[str, str] | None = None,
) -> tuple[Path, str, int]:
    values = os.environ if environment is None else environment
    root = Path(
        values.get(
            "SNOTES_GALE_R1B_OUTBOX_ROOT",
            str(DEFAULT_OUTBOX_ROOT),
        )
    )
    outbox = Path(
        values.get(
            "SNOTES_GALE_R1B_OUTBOX_PATH",
            str(root / "outbox.db"),
        )
    )
    if not root.is_absolute() or not outbox.is_absolute():
        raise GaleSubmissionStagingError("outbox_path_not_absolute")
    if outbox.name != "outbox.db":
        raise GaleSubmissionStagingError("unexpected_outbox_name")
    try:
        if not outbox.resolve(strict=False).is_relative_to(
            root.resolve(strict=False)
        ):
            raise GaleSubmissionStagingError("outbox_outside_root")
    except OSError as error:
        raise GaleSubmissionStagingError("outbox_path_invalid") from error

    host = values.get("SNOTES_GALE_R1B_HOST", DEFAULT_HOST).strip()
    if host != DEFAULT_HOST:
        raise GaleSubmissionStagingError("host_must_be_loopback")
    try:
        port = int(values.get("SNOTES_GALE_R1B_PORT", str(DEFAULT_PORT)))
    except ValueError as error:
        raise GaleSubmissionStagingError("invalid_port") from error
    if not 1 <= port <= 65535:
        raise GaleSubmissionStagingError("invalid_port")
    return outbox, host, port


def build_server(database_path: Path) -> FastMCP:
    initialize_outbox(database_path)
    server = FastMCP(
        name="S-notes Gale R1B Submission Staging",
        instructions=(
            "Fictional-data staging only. Create a new pending submission in "
            "the isolated Oracle outbox. This service cannot read or modify "
            "S-notes notes or Gale mirrors."
        ),
        mask_error_details=True,
        strict_input_validation=True,
        on_duplicate="error",
    )
    annotations = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )

    @server.tool(
        name="create_note_submission",
        title="Create a fictional Gale submission",
        description=(
            "Create one fictional staging submission. Supplying the same "
            "idempotency_key returns the original submission UUID."
        ),
        annotations=annotations,
    )
    def create_note_submission(
        title: TitleText,
        content: ContentText,
        suggested_directory: DirectoryText = "",
        suggested_tags: list[TagText] | None = None,
        idempotency_key: IdempotencyText | None = None,
    ) -> CreateSubmissionResponse:
        started_at = time.monotonic()
        try:
            if suggested_tags is not None and len(suggested_tags) > MAX_TAG_COUNT:
                raise GaleSubmissionOutboxError("too_many_tags")
            submission, duplicate = create_submission(
                database_path,
                title=title,
                content=content,
                suggested_directory=suggested_directory,
                suggested_tags=suggested_tags or [],
                idempotency_key=idempotency_key,
            )
            _log_tool_event(
                status="ok",
                duplicate=duplicate,
                latency_ms=_latency_ms(started_at),
            )
            return CreateSubmissionResponse(
                status="ok",
                submission_uuid=submission.submission_uuid,
                external_submission_id=submission.external_submission_id,
                delivery_state=submission.delivery_state,
                duplicate=duplicate,
            )
        except GaleSubmissionOutboxError as error:
            _log_tool_event(
                status="failed",
                duplicate=False,
                latency_ms=_latency_ms(started_at),
                error_code=error.code,
            )
            return CreateSubmissionResponse(
                status="failed",
                reason=error.code,
            )

    @server.custom_route(
        "/healthz",
        methods=["GET"],
        include_in_schema=False,
    )
    async def health(_request: Request) -> JSONResponse:
        try:
            counts = outbox_status(database_path)
        except Exception:
            return JSONResponse(
                {"status": "failed", "error_code": "outbox_unavailable"},
                status_code=503,
            )
        return JSONResponse(
            {
                "status": "ok",
                "queued": counts["queued"],
                "delivered": counts["delivered"],
                "acknowledged": counts["acknowledged"],
                "tools": 1,
            }
        )
    return server


def build_app(database_path: Path):
    return build_server(database_path).http_app(
        path="/mcp",
        json_response=True,
        stateless_http=True,
    )


def _configure_safe_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for logger_name in ("fastmcp", "mcp", "uvicorn.error"):
        logging.getLogger(logger_name).setLevel(logging.CRITICAL)


def _log_tool_event(
    *,
    status: str,
    duplicate: bool,
    latency_ms: int,
    error_code: str = "",
) -> None:
    LOGGER.info(
        "submission_tool_event status=%s duplicate=%s latency_ms=%d error_code=%s",
        status,
        "yes" if duplicate else "no",
        max(0, int(latency_ms)),
        error_code or "none",
    )


def _latency_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog="snotes-gale-r1b-staging")
    parser.add_argument(
        "command",
        choices=("serve", "doctor"),
        nargs="?",
        default="serve",
    )
    args = parser.parse_args(argv)
    _configure_safe_logging()
    try:
        database_path, host, port = load_staging_config()
        initialize_outbox(database_path)
        if args.command == "doctor":
            counts = outbox_status(database_path)
            print("GALE_R1B_STAGING_STATUS=PASS")
            print("HOST=127.0.0.1")
            print(f"PORT={port}")
            print("TOOLS_EXPOSED=1")
            print(f"QUEUED_COUNT={counts['queued']}")
            print(f"DELIVERED_COUNT={counts['delivered']}")
            print(f"ACKNOWLEDGED_COUNT={counts['acknowledged']}")
            return 0
        uvicorn.run(
            build_app(database_path),
            host=host,
            port=port,
            access_log=False,
            server_header=False,
            proxy_headers=False,
            log_level="warning",
        )
        return 0
    except (
        GaleSubmissionOutboxError,
        GaleSubmissionStagingError,
    ) as error:
        sys.stderr.write(
            "GALE_R1B_STAGING_STATUS=FAIL\n"
            f"ERROR_CODE={error.code}\n"
        )
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception:
        sys.stderr.write(
            "GALE_R1B_STAGING_STATUS=FAIL\n"
            "ERROR_CODE=unexpected_failure\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
