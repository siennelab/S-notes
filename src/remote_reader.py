"""Fixed production entry point: one worker, loopback only, no access logs."""
from src.snotes.application.reader_service import ReaderService
from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
from src.snotes.transport.reader_auth import MIRROR_PATH, load_settings
from src.snotes.transport.reader_web import create_reader_app


def main():
    import uvicorn
    settings = load_settings()
    service = ReaderService(SQLiteCloudMirrorReader(MIRROR_PATH))
    uvicorn.run(create_reader_app(settings, service), host="127.0.0.1", port=settings.port,
                workers=1, access_log=False, server_header=False, log_level="warning")


if __name__ == "__main__":
    main()
