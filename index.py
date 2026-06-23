"""Compatibility entrypoint and exports for vector index imports."""

from indexing.vector_index import *  # noqa: F401,F403
from indexing.vector_index import main


if __name__ == "__main__":
    raise SystemExit(main())

