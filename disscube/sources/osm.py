"""Vector features from OpenStreetMap, through the Overpass API.

A pipeline asks for ways by Overpass statements and gets them clipped to the grid
(plus a margin) as a vector source with a checksum and provenance::

    [[source]]
    id     = "roads"
    type   = "osm"
    query  = 'way["highway"]["ref"~"BR-163"]'
    margin = 0.7                       # degrees beyond the grid: distances need far features

The statements are Overpass QL without the bounding box, which is added to each one
(``way[...](south,west,north,east)``), so ``query`` can hold several, separated by ``;``
or given as a list. Only ways are read; they come back as ``LineString`` in EPSG:4326
(a closed way, such as a reservoir, stays as its outline).

**Reproducibility.** OpenStreetMap is a moving target. The answer is saved once in a
cache (``$DISSCUBE_CACHE/osm``, or ``~/.cache/disscube/osm``) keyed by what was asked
(statements, bounding box, date), and every later run reuses it: the same pipeline gives
the same bytes, hence the same ``spec_hash``, until the cache is deleted. Its checksum and
the time it was retrieved go to the provenance. ``date`` asks Overpass for the map as it
was on that day (``[date:"..."]``), which keeps later roads out of an earlier period.

**Etiquette.** Overpass answers HTTP 406 to clients that do not identify themselves, so set
``OSM_CONTACT`` (an e-mail or URL) in the environment; it goes in the ``User-Agent``. Several
public servers are tried in turn, with retries. Data © OpenStreetMap contributors (ODbL).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from disscube.utils import sha256_file

log = logging.getLogger("disscube.sources.osm")

#: Public Overpass servers, tried in turn (the main one often answers 504 when busy).
DEFAULT_ENDPOINTS: tuple[str, ...] = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
#: Passes over the endpoints before giving up.
ROUNDS = 3
#: Seconds the Overpass query may run server-side.
QUERY_TIMEOUT = 180
LICENSE = "ODbL"
ATTRIBUTION = "© OpenStreetMap contributors"
#: Tags kept as feature properties.
KEPT_TAGS = ("name", "ref", "highway", "waterway", "natural", "railway", "boundary")

# Seconds to wait between attempts; replaced in tests.
_sleep: Callable[[float], None] = time.sleep


class OsmError(RuntimeError):
    """The OpenStreetMap data could not be obtained."""


def default_cache_dir() -> Path:
    """``$DISSCUBE_CACHE/osm``, or ``~/.cache/disscube/osm``."""
    root = os.environ.get("DISSCUBE_CACHE") or Path.home() / ".cache" / "disscube"
    return Path(root) / "osm"


def user_agent() -> str:
    contact = os.environ.get("OSM_CONTACT", "").strip()
    if not contact:
        log.warning("OSM_CONTACT is not set: public Overpass servers reject clients without a contact (HTTP 406)")
    return f"disscube ({contact or 'no contact given'})"


def statements_of(query: str | Sequence[str]) -> list[str]:
    """The Overpass statements of ``query`` (a string split at ``;``, or a list), stripped."""
    parts = query.split(";") if isinstance(query, str) else list(query)
    statements = [p.strip().rstrip(";").strip() for p in parts if p and p.strip()]
    if not statements:
        raise ValueError("an osm source needs a non-empty 'query'")
    for st in statements:
        if st.split("[", 1)[0].strip() != "way":
            raise ValueError(f"an osm statement must filter ways, e.g. way[\"highway\"]: {st!r}")
    return statements


def padded(bbox_geo: Sequence[float], margin: float) -> list[float]:
    """``[west, south, east, north]`` widened by ``margin`` degrees, clipped to the globe."""
    w, s, e, n = (float(v) for v in bbox_geo)
    return [max(w - margin, -180.0), max(s - margin, -90.0), min(e + margin, 180.0), min(n + margin, 90.0)]


def build_query(statements: Sequence[str], bbox: Sequence[float], date: str | None = None,
                timeout: int = QUERY_TIMEOUT) -> str:
    """The Overpass QL text asking for ``statements`` inside ``bbox`` (``[w, s, e, n]``)."""
    w, s, e, n = bbox
    header = f"[out:json][timeout:{timeout}]"
    if date:
        header += f'[date:"{_overpass_date(date)}"]'
    body = "".join(f"{st}({s},{w},{n},{e});" for st in statements)
    return f"{header};({body});out geom;"


def _overpass_date(date: str) -> str:
    """``2019-01-01`` or a full ISO timestamp, as Overpass wants it (``YYYY-MM-DDThh:mm:ssZ``)."""
    try:
        parsed = datetime.fromisoformat(date)
    except ValueError:
        raise ValueError(f"osm 'date' must be ISO 8601 (e.g. 2019-01-01): {date!r}") from None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_http(url: str) -> str:
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise ValueError(f"Overpass endpoint must be http(s): {url!r}")
    return url


def overpass(query: str, endpoints: Sequence[str] = DEFAULT_ENDPOINTS, timeout: int = QUERY_TIMEOUT,
             rounds: int = ROUNDS) -> tuple[dict[str, Any], str]:
    """Run ``query`` on the first endpoint that answers; returns ``(result, endpoint)``.

    Failures (HTTP 5xx, timeouts, a server-side ``remark`` with no elements) move on to the
    next endpoint; after a full pass it waits and tries again, ``rounds`` times. HTTP 406
    stops at once: it means the client is not identified (set ``OSM_CONTACT``).
    """
    data = urllib.parse.urlencode({"data": query}).encode()
    agent = user_agent()
    errors: list[str] = []
    for rnd in range(rounds):
        for url in endpoints:
            req = urllib.request.Request(_require_http(url), data=data, headers={"User-Agent": agent})
            host = urllib.parse.urlparse(url).netloc
            try:
                with urllib.request.urlopen(req, timeout=timeout + 30) as resp:  # nosec B310
                    result = json.load(resp)
                if "remark" in result and not result.get("elements"):
                    raise OsmError(str(result["remark"]))  # server-side timeout / out of memory
                return result, url
            except urllib.error.HTTPError as exc:
                if exc.code == 406:
                    raise OsmError("HTTP 406 from Overpass: set OSM_CONTACT (an e-mail or URL) so it can identify you") from exc
                errors.append(f"{host}: HTTP {exc.code}")
            except Exception as exc:  # noqa: BLE001 — any failure of one server: try the next
                errors.append(f"{host}: {type(exc).__name__}: {exc}")
            log.warning("%s", errors[-1])
            _sleep(3)
        if rnd < rounds - 1:
            _sleep(10 * (rnd + 1))
    raise OsmError("Overpass did not answer: " + "; ".join(errors[-len(endpoints):]))


def to_geojson(result: dict[str, Any]) -> dict[str, Any]:
    """Overpass ``out geom`` ways as a GeoJSON FeatureCollection of ``LineString`` (EPSG:4326)."""
    features = []
    for el in result.get("elements", []):
        geom = el.get("geometry")
        if el.get("type") != "way" or not geom or len(geom) < 2:
            continue
        tags = el.get("tags", {})
        features.append({
            "type": "Feature",
            "properties": {"osm_id": el["id"], **{k: tags[k] for k in KEPT_TAGS if k in tags}},
            "geometry": {"type": "LineString", "coordinates": [[p["lon"], p["lat"]] for p in geom]},
        })
    return {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "features": features}


def cache_key(statements: Sequence[str], bbox: Sequence[float], date: str | None) -> str:
    """What was asked, as a short hash: the same request finds the same cached answer."""
    request = {"statements": list(statements), "bbox": [round(float(v), 6) for v in bbox],
               "date": _overpass_date(date) if date else None}
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:16]


def fetch(query: str | Sequence[str], bbox_geo: Sequence[float], margin: float = 0.0, date: str | None = None,
          endpoints: Sequence[str] | None = None, cache: str | Path | None = None) -> tuple[Path, dict[str, Any]]:
    """The GeoJSON file for ``query`` over ``bbox_geo`` (+ ``margin``) and what is known about it.

    Returns ``(path, info)``. A request already in the cache is not repeated. An empty answer
    raises :class:`OsmError` and is not cached: it almost always means a wrong query or area.
    """
    statements = statements_of(query)
    bbox = padded(bbox_geo, margin)
    key = cache_key(statements, bbox, date)
    folder = Path(cache) if cache else default_cache_dir()
    path, meta_path = folder / f"osm-{key}.geojson", folder / f"osm-{key}.json"
    if path.exists() and meta_path.exists():
        log.info("OpenStreetMap: reusing %s", path)
        return path, json.loads(meta_path.read_text(encoding="utf-8"))

    result, endpoint = overpass(build_query(statements, bbox, date), tuple(endpoints or DEFAULT_ENDPOINTS))
    collection = to_geojson(result)
    if not collection["features"]:
        raise OsmError(f"OpenStreetMap returned no ways for {statements} in {bbox}"
                       + (f" as of {date}" if date else "") + ": check the query and the area")

    folder.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(collection), encoding="utf-8")
    tmp.replace(path)
    info = {"statements": statements, "bbox": bbox, "margin": margin, "date": date, "endpoint": endpoint,
            "retrieved": datetime.now(UTC).isoformat(timespec="seconds"), "features": len(collection["features"]),
            "checksum": sha256_file(path), "license": LICENSE, "attribution": ATTRIBUTION,
            "osm_base": result.get("osm3s", {}).get("timestamp_osm_base")}
    meta_path.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    return path, info


def register_osm_source(cube, source_id: str, query: str | Sequence[str], bbox_geo: Sequence[float],
                        out_dir: str | Path, margin: float = 0.0, date: str | None = None,
                        endpoints: Sequence[str] | None = None, cache: str | Path | None = None,
                        time: int | None = None, name: str | None = None):
    """Fetch (or reuse) the OpenStreetMap ways and register them as a vector source.

    The GeoJSON is copied to ``out_dir/<source_id>.geojson`` next to a
    ``<source_id>.provenance.json`` with the request, the server, the time retrieved, the
    ``timestamp_osm_base`` of the data and the checksum.
    """
    from disscube.models import SpatialSource

    cached, info = fetch(query, bbox_geo, margin=margin, date=date, endpoints=endpoints, cache=cache)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{source_id}.geojson"
    path.write_bytes(cached.read_bytes())
    prov_path = out / f"{source_id}.provenance.json"
    provenance = {"type": "osm", "file": str(path), "crs": "EPSG:4326", "format": "vector", **info}
    prov_path.write_text(json.dumps(provenance, indent=2, ensure_ascii=False), encoding="utf-8")
    src = SpatialSource(id=source_id, name=name or source_id, format="vector", asset_url=str(path),
                        crs="EPSG:4326", time=time, checksum=info["checksum"],
                        tags=["osm", f"provenance:{prov_path}"])
    cube.register_spatial_source(src)
    return src
