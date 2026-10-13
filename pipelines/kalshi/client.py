"""Public Kalshi acquisition using the same hash-verified cache as FIFA."""

import threading
import time
import logging
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from pipelines.fifa.client import AcquisitionError, Client

BASE = "https://api.elections.kalshi.com/trade-api/v2/"


class KalshiClient(Client):
    def __init__(self, *args, **kwargs):
        self.lock = threading.Lock()
        self.next_request = 0.0
        self.interval = 0.2
        self.cooldown_until = 0.0
        self.transport = kwargs.pop("opener", urlopen)
        super().__init__(*args, opener=self.throttled_open, **kwargs)

    def throttled_open(self, request, timeout=45):
        # Throttle retries as well as initial requests across every worker.
        with self.lock:
            time.sleep(max(0, self.next_request - time.monotonic()))
            self.next_request = time.monotonic() + self.interval
        try:
            return self.transport(request, timeout=timeout)
        except HTTPError as error:
            if error.code == 429:
                with self.lock:
                    now = time.monotonic()
                    # Concurrent in-flight responses belong to the same burst;
                    # apply one shared backoff, not one multiplier per worker.
                    if now >= self.cooldown_until:
                        self.interval = min(2.0, self.interval * 1.5)
                        self.cooldown_until = now + 30
                        logging.warning(
                            "Kalshi rate limit: pausing 30s; request interval %.3fs",
                            self.interval,
                        )
                    self.next_request = max(self.next_request, self.cooldown_until)
            raise

    def request(self, path, **params):
        url = BASE + path + ("?" + urlencode(params) if params else "")
        return self.get(url)

    def pages(self, path, key, **params):
        cursor, seen = None, set()
        for _ in range(10000):
            query = dict(params, limit=1000)
            if cursor:
                query["cursor"] = cursor
            data, meta = self.request(path, **query)
            if not isinstance(data.get(key), list):
                raise AcquisitionError("Missing paginated " + key)
            yield data[key], meta
            cursor = data.get("cursor")
            if not cursor:
                return
            if cursor in seen or not data[key]:
                raise AcquisitionError("Repeated cursor or empty nonterminal page")
            seen.add(cursor)
        raise AcquisitionError("Pagination did not terminate")
