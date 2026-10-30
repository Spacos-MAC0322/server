"""Environment configuration shared by application infrastructure."""

import os

DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


def get_cors_origins() -> list[str]:
    """Read comma-separated origins; an empty value disables cross-origin access."""
    origins = [
        origin.strip()
        for origin in os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
        if origin.strip()
    ]
    if "*" in origins:
        raise ValueError("CORS_ORIGINS must list explicit origins instead of '*'.")
    return origins
