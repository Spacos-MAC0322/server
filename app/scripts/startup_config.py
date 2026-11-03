"""Expose only non-secret launch values to the Bash startup script."""

from app.core.config import StartupSettings


def main() -> None:
    settings = StartupSettings()
    is_default_local = settings.mongo_uri.get_secret_value().rstrip("/") in {
        "mongodb://localhost:27017",
        "mongodb://127.0.0.1:27017",
    }
    print(settings.api_host, settings.api_port, int(is_default_local))


if __name__ == "__main__":
    main()
