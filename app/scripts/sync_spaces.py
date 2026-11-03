"""Compatibility entry point; the pipeline lives in app.jobs.sync_places."""

from app.jobs.sync_places import main as main
from app.jobs.sync_places import run as run

if __name__ == "__main__":
    main()
