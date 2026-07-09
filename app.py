from src.database import initialize_database
from src.main_window import run_app


def main() -> int:
    initialize_database()
    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
