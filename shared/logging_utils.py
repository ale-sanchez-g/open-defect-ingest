"""Reusable logger setup for Datadog-visible stdout logs."""

from __future__ import annotations

import logging
import sys


def get_datadog_logger(logger_name: str, service_name: str, level: int = logging.INFO) -> logging.Logger:
    """Return a logger that always emits to stdout with stable formatting."""
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)

    if not logger.handlers:
        # Datadog's Docker log pipeline can map stderr entries to error status.
        # Emit app logs to stdout so INFO/WARN levels are preserved correctly.
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(level)
        stream_handler.setFormatter(
            logging.Formatter(
                f"%(asctime)s [%(levelname)s] {service_name} %(name)s: %(message)s"
            )
        )
        logger.addHandler(stream_handler)

    logger.propagate = False
    return logger
