import re
import sqlite3
import time
from collections import OrderedDict
from collections.abc import Callable
from threading import Lock
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel

from revival_radar.chains import CHAINS
from revival_radar.web.models import (
    AlertDetail,
    AlertsPage,
    MarketReport,
    Metadata,
    Outcomes,
    RadarPage,
    TokenDetail,
    TokenHistory,
)
from revival_radar.web.queries import PublicQueries, public_reader

router = APIRouter(prefix="/api/v1", tags=["public"])
Chain = Literal["sol", "bsc", "base", "robinhood", "arc"]
Stage = Literal[
    "IGNORE",
    "WATCH",
    "EARLY_REVIVAL",
    "REVIVING",
    "STRONG_REVIVAL",
    "CONFIRMED_REVIVAL",
]
Cutoff = Annotated[float | None, Query(ge=0, allow_inf_nan=False)]
Page = Annotated[int, Query(ge=1, le=10000)]
PageSize = Annotated[int, Query(ge=1, le=100)]


class ResponseCache:
    """Small process-local cache. Only explicit public models may be stored."""

    def __init__(self):
        self.values: OrderedDict[tuple, tuple[float, BaseModel]] = OrderedDict()
        self.lock = Lock()

    def get(self, key: tuple) -> BaseModel | None:
        with self.lock:
            value = self.values.get(key)
            if value is None:
                return None
            if value[0] < time.monotonic():
                del self.values[key]
                return None
            self.values.move_to_end(key)
            return value[1]

    def put(self, key: tuple, value: BaseModel, ttl: int) -> None:
        if not ttl:
            return
        with self.lock:
            self.values[key] = (time.monotonic() + ttl, value)
            self.values.move_to_end(key)
            while len(self.values) > 256:
                self.values.popitem(last=False)


def cutoff(request: Request, as_of: float | None) -> float:
    now = request.app.state.clock()
    if as_of is not None and as_of > now:
        raise HTTPException(status_code=422, detail="as_of cannot be in the future")
    return now if as_of is None else as_of


def address_for(chain: str, address: str) -> str:
    pattern = r"[1-9A-HJ-NP-Za-km-z]{32,44}" if chain == "sol" else r"0x[0-9a-fA-F]{40}"
    if not re.fullmatch(pattern, address):
        raise HTTPException(status_code=422, detail="Invalid contract address for chain")
    return address if chain == "sol" else address.lower()


def read[T: BaseModel](
    request: Request,
    as_of: float | None,
    key: tuple,
    query: Callable[[PublicQueries, float], T],
) -> T:
    until = cutoff(request, as_of)
    try:
        with public_reader(request.app.state.config) as queries:
            cache_key = (key, as_of, queries.version(until))
            cached = request.app.state.public_cache.get(cache_key)
            if cached is not None:
                return cached
            result = query(queries, until)
            if result is None:
                raise HTTPException(status_code=404, detail="Saved record not found")
            request.app.state.public_cache.put(
                cache_key,
                result,
                request.app.state.config.web_cache_seconds,
            )
            return result
    except sqlite3.Error:
        raise HTTPException(
            status_code=503, detail="Saved observations temporarily unavailable"
        ) from None


@router.get("/radar", response_model=RadarPage)
def radar(
    request: Request,
    chain: Chain | None = None,
    status: Stage | None = None,
    search: Annotated[str, Query(max_length=100)] = "",
    min_score: Annotated[int, Query(ge=0, le=100)] = 0,
    include_ignore: bool = False,
    sort: Literal["score", "timestamp", "market_cap", "liquidity", "volume_5m"] = "score",
    order: Literal["asc", "desc"] = "desc",
    page: Page = 1,
    page_size: PageSize = 25,
    as_of: Cutoff = None,
) -> RadarPage:
    filters = dict(
        chain=chain,
        status=status,
        search=search.strip(),
        min_score=min_score,
        include_ignore=include_ignore,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
    return read(
        request,
        as_of,
        ("radar", *filters.values()),
        lambda queries, until: queries.radar(until, **filters),
    )


@router.get("/tokens/{chain}/{address}", response_model=TokenDetail)
def token(request: Request, chain: Chain, address: str, as_of: Cutoff = None) -> TokenDetail:
    address = address_for(chain, address)
    return read(
        request,
        as_of,
        ("token", chain, address),
        lambda queries, until: queries.token(chain, address, until),
    )


@router.get("/tokens/{chain}/{address}/history", response_model=TokenHistory)
def history(
    request: Request,
    chain: Chain,
    address: str,
    hours: Annotated[int, Query(ge=1, le=168)] = 168,
    max_points: Annotated[int, Query(ge=1, le=1000)] = 500,
    as_of: Cutoff = None,
) -> TokenHistory:
    address = address_for(chain, address)
    return read(
        request,
        as_of,
        ("history", chain, address, hours, max_points),
        lambda queries, until: queries.history(chain, address, until, hours, max_points),
    )


@router.get("/alerts", response_model=AlertsPage)
def alerts(
    request: Request,
    chain: Chain | None = None,
    page: Page = 1,
    page_size: PageSize = 25,
    as_of: Cutoff = None,
) -> AlertsPage:
    return read(
        request,
        as_of,
        ("alerts", chain, page, page_size),
        lambda queries, until: queries.alerts(until, chain, page, page_size),
    )


@router.get("/alerts/{identity}", response_model=AlertDetail)
def alert(
    request: Request,
    identity: Annotated[int, Path(ge=1, lt=2**63)],
    as_of: Cutoff = None,
) -> AlertDetail:
    return read(
        request, as_of, ("alert", identity), lambda queries, until: queries.alert(identity, until)
    )


@router.get("/reports", response_model=MarketReport)
def reports(
    request: Request,
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
    as_of: Cutoff = None,
) -> MarketReport:
    if hours not in (1, 4, 12, 24, 72, 168):
        raise HTTPException(status_code=422, detail="Supported report windows: 1,4,12,24,72,168")
    return read(
        request, as_of, ("reports", hours), lambda queries, until: queries.report(until, hours)
    )


@router.get("/outcomes", response_model=Outcomes)
def outcomes(
    request: Request,
    days: Annotated[int, Query(ge=1, le=7)] = 7,
    as_of: Cutoff = None,
) -> Outcomes:
    return read(
        request, as_of, ("outcomes", days), lambda queries, until: queries.outcomes(until, days)
    )


@router.get("/meta", response_model=Metadata)
def metadata(request: Request, as_of: Cutoff = None) -> Metadata:
    def result(queries: PublicQueries, until: float) -> Metadata:
        rows = queries.db.execute(
            """SELECT DISTINCT e.chain FROM evaluations e JOIN scan_runs s ON s.id=e.scan_id
            WHERE e.timestamp>? AND e.timestamp<=? AND s.finished IS NOT NULL AND s.finished<=?
            ORDER BY e.chain""",
            (until - 7 * 86400, until, until),
        ).fetchall()
        telegram = request.app.state.config.web_telegram_url
        return Metadata(
            chains=[
                {"id": row[0], "name": CHAINS[row[0]].display_name}
                for row in rows
                if row[0] in CHAINS
            ],
            telegram_url=telegram if telegram.startswith("https://t.me/") else None,
            as_of=until,
            last_scan_at=queries.last_scan_at(until),
        )

    return read(request, as_of, ("meta",), result)
