"""python -m analyzer: serve the HTTP API (see server.py)."""
import logging

import uvicorn

from . import config


def main():
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    # One worker on purpose: the metadata APIs' rate limits are enforced in-process.
    uvicorn.run("analyzer.server:app", host="0.0.0.0", port=config.PORT, workers=1, log_config=None)


if __name__ == "__main__":
    main()
