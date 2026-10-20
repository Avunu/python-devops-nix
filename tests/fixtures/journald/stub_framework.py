"""A framework's logging, for the journald graft test: a stderr handler in
its own terminal format, and a function that logs through it."""

import logging
import sys

handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
)
logger = logging.getLogger("stub.app")
logger.addHandler(handler)
logger.propagate = False
logger.setLevel(logging.DEBUG)


def log():
    logger.info("\x1b[32mstarted\x1b[0m")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("request failed")
