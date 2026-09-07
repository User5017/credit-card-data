"""One HTTP session for every fetcher: identifies itself, retries transient failures, never hangs."""

from __future__ import annotations

import os

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__

DEFAULT_TIMEOUT = 60


class _TimeoutAdapter(HTTPAdapter):
    def send(self, request, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        return super().send(request, **kwargs)


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = _TimeoutAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    contact = os.environ.get("CARDDASH_CONTACT", "")
    ua = f"carddash/{__version__} (+https://github.com/User5017/credit-card-data)"
    if contact:
        ua += f" {contact}"
    session.headers["User-Agent"] = ua
    return session
