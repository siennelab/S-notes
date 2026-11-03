"""Fixed production entry point: one worker, loopback only, no access logs."""
from src.snotes.transport.reader_auth import load_settings
from src.snotes.transport.reader_web import create_reader_app


def main():
    import uvicorn
    settings = load_settings()
    uvicorn.run(create_reader_app(settings), host="127.0.0.1", port=settings.port,
                workers=1, access_log=False, server_header=False, log_level="warning")


if __name__ == "__main__":
    main()
