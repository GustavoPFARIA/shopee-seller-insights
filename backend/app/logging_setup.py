"""Logging configuration shared by the API and the worker."""

import logging

# HTTP client libraries log full request URLs at INFO. Shopee requires the shop
# access_token in the query string, so those lines would leak credentials.
NOISY_HTTP_LOGGERS = ("httpx", "httpx2", "httpcore", "httpcore2")


def quiet_http_clients() -> None:
    for name in NOISY_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    quiet_http_clients()
