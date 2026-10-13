"""Public HTTP client with verified immutable payloads and URL-keyed cache."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import zlib
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

BASE = "https://api.fifa.com/api/v3/"
COHORTS = {
    "premier-league-2025-26": ("2000000000", "51r6ph2woavlbbpk8f29nynf8", 380),
    "world-cup-2026": ("17", "285023", 104),
}


class AcquisitionError(RuntimeError):
    """A response cannot be trusted; abort publication rather than exclude data."""


class CacheIntegrityError(AcquisitionError):
    """A cached response or its request identity failed integrity checks."""


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def atomic_bytes(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_bytes(path, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode())


class Client:
    response_types = (dict,)

    def __init__(
        self, root, refresh=False, offline=False, opener=urlopen, sleep=time.sleep
    ):
        if refresh and offline:
            raise ValueError("--refresh and --offline are mutually exclusive")
        self.root = Path(root)
        self.refresh, self.offline = refresh, offline
        self.opener, self.sleep = opener, sleep

    def _index(self, url):
        return (
            self.root
            / "requests"
            / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        )

    def store(self, url, body, fetched_at=None, **extra):
        cache_key = extra.pop("cache_key", url)
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError) as error:
            raise AcquisitionError("Invalid JSON response: " + url) from error
        if not isinstance(data, self.response_types):
            raise AcquisitionError("Unexpected JSON response type: " + url)
        digest = hashlib.sha256(body).hexdigest()
        payload = self.root / (digest + ".json.gz")
        if payload.exists():
            try:
                existing = gzip.decompress(payload.read_bytes())
            except (OSError, EOFError, zlib.error) as error:
                raise CacheIntegrityError(
                    "Existing raw payload is corrupt: " + digest
                ) from error
            if hashlib.sha256(existing).hexdigest() != digest:
                raise CacheIntegrityError("Existing raw payload is corrupt: " + digest)
        else:
            atomic_bytes(payload, gzip.compress(body, mtime=0))
        metadata = {
            "url": url,
            "sha256": digest,
            "bytes": len(body),
            "http_status": 200,
            "fetched_at_utc": fetched_at,
            "raw_file": payload.name,
            **extra,
        }
        write_json(self._index(cache_key), metadata)
        return data, metadata

    def get(self, url, *, json_body=None):
        # Some public read endpoints use POST; key the cache on method and body.
        request_body = dumps(json_body).encode() if json_body is not None else None
        cache_key = (
            url if request_body is None else url + "#POST:" + request_body.decode()
        )
        index = self._index(cache_key)
        if index.exists() and not self.refresh:
            try:
                metadata = json.loads(index.read_text())
                body = gzip.decompress((self.root / metadata["raw_file"]).read_bytes())
                if (
                    metadata["url"] != url
                    or metadata.get("request_body") != json_body
                    or hashlib.sha256(body).hexdigest() != metadata["sha256"]
                ):
                    raise CacheIntegrityError("Cache integrity mismatch: " + url)
                data = json.loads(body)
                if not isinstance(data, self.response_types):
                    raise CacheIntegrityError(
                        "Unexpected cached JSON response type: " + url
                    )
            except (
                OSError,
                EOFError,
                zlib.error,
                ValueError,
                KeyError,
                TypeError,
            ) as error:
                raise CacheIntegrityError("Cache integrity mismatch: " + url) from error
            return data, metadata
        if self.offline:
            raise FileNotFoundError("Uncached URL in offline mode: " + url)
        for attempt in range(4):
            try:
                with self.opener(
                    Request(
                        url,
                        data=request_body,
                        headers={
                            "User-Agent": "rin-gym-fifa/1.0",
                            "Accept": "application/json",
                            **(
                                {"Content-Type": "application/json"}
                                if request_body is not None
                                else {}
                            ),
                        },
                    ),
                    timeout=45,
                ) as response:
                    body = response.read()
                return self.store(
                    url,
                    body,
                    datetime.now(timezone.utc).isoformat(),
                    cache_key=cache_key,
                    **(
                        {"request_method": "POST", "request_body": json_body}
                        if request_body is not None
                        else {}
                    ),
                )
            except (HTTPError, URLError, TimeoutError) as error:
                status = getattr(error, "code", None)
                if attempt == 3 or (
                    status is not None and status not in (408, 429, 500, 502, 503, 504)
                ):
                    raise
                delay = 2**attempt
                retry = (
                    (error.headers or {}).get("Retry-After")
                    if isinstance(error, HTTPError)
                    else None
                )
                if retry:
                    try:
                        delay = max(delay, float(retry))
                    except ValueError:
                        delay = max(
                            delay,
                            (
                                parsedate_to_datetime(retry)
                                - datetime.now(timezone.utc)
                            ).total_seconds(),
                        )
                self.sleep(max(0, delay))

    def calendar(self, competition, season):
        params = {
            "language": "en",
            "count": 500,
            "idCompetition": competition,
            "idSeason": season,
        }
        matches, seen, tokens, sources = [], set(), set(), []
        for _ in range(1000):
            url = BASE + "calendar/matches?" + urlencode(params)
            data, metadata = self.get(url)
            sources.append(metadata)
            if not isinstance(data.get("Results"), list):
                raise ValueError("Calendar Results must be an array")
            for match in data["Results"]:
                if (
                    str(match.get("IdCompetition")) != competition
                    or str(match.get("IdSeason")) != season
                ):
                    raise ValueError("Calendar returned a different competition/season")
                mid = str(match["IdMatch"])
                if mid in seen:
                    raise ValueError("Duplicate calendar match: " + mid)
                seen.add(mid)
                matches.append((match, metadata))
            token = data.get("ContinuationToken")
            if not token:
                return matches, sources
            if token in tokens:
                raise ValueError("Repeated calendar continuation token")
            tokens.add(token)
            params["continuationToken"] = token
            if data.get("ContinuationHash"):
                params["continuationHash"] = data["ContinuationHash"]
            else:
                params.pop("continuationHash", None)
        raise ValueError("Calendar pagination exceeded 1000 pages")

    def timelines(self, matches, workers=4):
        def one(item):
            match, calendar_meta = item
            mid = str(match["IdMatch"])
            try:
                timeline, metadata = self.get(
                    BASE + "timelines/" + mid + "?language=en"
                )
                return match, calendar_meta, timeline, metadata, None
            except (OSError, ValueError, URLError) as error:
                return match, calendar_meta, None, None, str(error)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            yield from pool.map(one, matches)

    def import_cache(self, directory):
        """Import the two earlier audit cache layouts, verifying original hashes."""
        count = 0
        for path in sorted(Path(directory).glob("*.json")):
            if any(
                path.name.endswith(x)
                for x in (".meta.json", ".http.json", ".error.json")
            ):
                continue
            if path.name not in (
                "fifa_calendar_2026.json",
                "calendar-season.json",
            ) and not (
                path.name.startswith("timeline-")
                or path.name.endswith("_timeline.json")
            ):
                continue
            metadata_path = next(
                (
                    p
                    for p in (
                        path.with_suffix(".http.json"),
                        path.with_suffix(".meta.json"),
                    )
                    if p.exists()
                ),
                None,
            )
            if metadata_path is None:
                continue
            metadata = json.loads(metadata_path.read_text())
            url = metadata.get("url", "")
            if urlsplit(url).hostname != "api.fifa.com":
                continue
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != metadata.get("sha256"):
                raise ValueError("Imported source hash mismatch: " + str(path))
            data = json.loads(body)
            if path.name == "fifa_calendar_2026.json":
                if (
                    data.get("ContinuationToken")
                    or len(data["Results"]) != 104
                    or any(
                        str(m["IdCompetition"]) != "17"
                        or str(m["IdSeason"]) != "285023"
                        for m in data["Results"]
                    )
                ):
                    raise ValueError(
                        "Legacy calendar is not the complete World Cup scope"
                    )
                url = (
                    BASE
                    + "calendar/matches?"
                    + urlencode(
                        dict(
                            language="en",
                            count=500,
                            idCompetition="17",
                            idSeason="285023",
                        )
                    )
                )
            fetched = (
                metadata.get("fetched_at_utc")
                or metadata.get("fetched_at")
                or metadata.get("retrieved_utc")
            )
            self.store(url, body, fetched, original_url=metadata["url"], imported=True)
            count += 1
        return count
