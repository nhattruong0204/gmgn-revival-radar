import json
import re
import sqlite3
import time
from collections import OrderedDict
from collections.abc import Callable
from threading import Condition, Lock
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel

from revival_radar.chains import CHAINS
from revival_radar.web.models import (
    AlertDetail,
    AlertsPage,
    BriefPage,
    CallJournal,
    MarketReport,
    Metadata,
    Outcomes,
    RadarPage,
    TokenDetail,
    TokenHistory,
    TrackRecord,
)
from revival_radar.web.queries import PublicQueries, finite, public_reader

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
        self.filling: set[tuple] = set()
        self.ready = Condition()

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

    def get_or_create(self, key: tuple, create: Callable[[], BaseModel], ttl: int) -> BaseModel:
        """Coalesce concurrent misses for one saved-data version, not unrelated reads."""
        if not ttl:
            return create()
        with self.ready:
            while key in self.filling:
                self.ready.wait()
            cached = self.get(key)
            if cached is not None:
                return cached
            self.filling.add(key)
        try:
            result = create()
            self.put(key, result, ttl)
            return result
        finally:
            with self.ready:
                self.filling.remove(key)
                self.ready.notify_all()


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
            cache_key = (key, as_of, queries.version(until, key[0]))

            def create():
                result = query(queries, until)
                if result is None:
                    raise HTTPException(status_code=404, detail="Saved record not found")
                queries.check_deadline()
                return result

            return request.app.state.public_cache.get_or_create(
                cache_key,
                create,
                0 if key[0] == "meta" else request.app.state.config.web_cache_seconds,
            )
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
    age_hours: Annotated[int, Query(ge=1, le=168)] = 24,
) -> RadarPage:
    if age_hours not in (1, 4, 24, 72, 168):
        raise HTTPException(status_code=422, detail="Unsupported observation window")
    filters = dict(
        age_hours=age_hours,
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


@router.get("/tokens/{chain}/{address}/briefs", response_model=BriefPage)
def briefs(
    request: Request,
    chain: Chain,
    address: str,
    page: Page = 1,
    page_size: PageSize = 10,
    as_of: Cutoff = None,
    version_id: Annotated[str | None, Query(pattern=r"^[a-f0-9]{32}$")] = None,
) -> BriefPage:
    address = address_for(chain, address)
    # Publication may occur without a changing cache; read the durable archive directly.
    until = cutoff(request, as_of)
    try:
        with public_reader(request.app.state.config) as queries:
            return queries.briefs(chain, address, until, page, page_size, version_id)
    except sqlite3.Error:
        raise HTTPException(
            status_code=503, detail="Saved briefs temporarily unavailable"
        ) from None


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
        # Stop at the first completed observation per supported chain instead
        # of walking every retained evaluation just to discover chain names.
        chains = [
            chain
            for chain in sorted(CHAINS)
            if queries.db.execute(
                """SELECT 1 FROM evaluations e JOIN scan_runs s ON s.id=e.scan_id
                WHERE e.chain=? AND e.timestamp>? AND e.timestamp<=?
                AND s.finished IS NOT NULL AND s.finished<=? LIMIT 1""",
                (chain, until - 7 * 86400, until, until),
            ).fetchone()
        ]
        worker = {}
        try:
            worker = json.loads(queries.repo.get_state("web:worker") or "{}")
            if not isinstance(worker, dict):
                worker = {}
        except (ValueError, TypeError):
            pass
        heartbeat = finite(worker.get("heartbeat_at"))
        fresh = (
            heartbeat is not None
            and 0 <= until - heartbeat <= 30
            and worker.get("stopped_at") is None
        )
        collection = worker.get("collection")
        configured = collection.get("active_chains") if isinstance(collection, dict) else None
        valid = (
            isinstance(configured, list)
            and all(c in CHAINS for c in configured if isinstance(c, str))
            and all(isinstance(c, str) for c in configured)
        )
        status = (
            "unknown"
            if not valid
            else "stale"
            if not fresh
            else "current"
            if configured
            else "disabled"
        )
        active = sorted(set(configured)) if status == "current" else []
        telegram = request.app.state.config.web_telegram_url
        return Metadata(
            active_chains=[{"id": chain, "name": CHAINS[chain].display_name} for chain in active],
            collection_status=status,
            worker_heartbeat_at=heartbeat if heartbeat is not None and heartbeat <= until else None,
            data_mode="synthetic" if request.app.state.config.web_allow_synthetic_data else "saved",
            chains=[{"id": chain, "name": CHAINS[chain].display_name} for chain in chains],
            telegram_url=telegram if telegram.startswith("https://t.me/") else None,
            as_of=until,
            last_scan_at=queries.last_scan_at(until),
        )

    return read(request, as_of, ("meta",), result)


@router.get("/track-record", response_model=TrackRecord)
@router.get("/track-record/export", response_model=None)
def track_record(
    request: Request,
    days: Annotated[int, Query(ge=1, le=365)] = 7,
    horizon: Annotated[int, Query(ge=1, le=72)] = 24,
    chain: Chain | None = None,
    preset: Literal["Strict", "Balanced", "Broad", "Custom", "unknown"] | None = None,
    revision: Annotated[str | None, Query(pattern=r"^(unknown|[0-9]{1,9})$")] = None,
    score_version: Annotated[str | None, Query(max_length=100)] = None,
    origin: Literal["live", "unknown", "synthetic"] | None = None,
    knowledge: Literal["recorded", "legacy_unknown"] | None = None,
    cohort: Annotated[str | None, Query(pattern=r"^[a-f0-9]{16}$")] = None,
    checkpoint: Literal["return", "missing_price", "unresolved", "pending", "missed"] | None = None,
    bucket: Annotated[int | None, Query(ge=0, le=4)] = None,
    page: Page = 1,
    page_size: PageSize = 25,
    as_of: Cutoff = None,
) -> TrackRecord | Response:
    from revival_radar.web.research_export import export_track_record

    until = cutoff(request, as_of)
    if horizon not in (1, 6, 24, 72):
        raise HTTPException(status_code=422, detail="Unsupported checkpoint horizon")
    filters = dict(
        chain=chain,
        preset=preset,
        revision=revision,
        score_version=score_version,
        origin=origin,
        knowledge=knowledge,
        cohort=cohort,
        checkpoint=checkpoint,
        bucket=bucket,
    )
    exporting = request.url.path.endswith("/export")
    if not exporting:
        return read(
            request,
            as_of,
            ("track-record", days, horizon, *filters.values(), page, page_size),
            lambda queries, at: queries.track_record(at, days, horizon, filters, page, page_size),
        )
    try:
        with public_reader(request.app.state.config) as queries:
            data = queries.track_record(
                until,
                days,
                horizon,
                filters,
                1 if exporting else page,
                10000 if exporting else page_size,
            )
        if exporting:
            return Response(
                export_track_record(data),
                media_type="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": 'attachment; filename="saved-checkpoints.csv"',
                    "Cache-Control": "no-store",
                },
            )
        return data
    except sqlite3.Error:
        raise HTTPException(
            status_code=503,
            detail="Saved cohort temporarily unavailable; retry or narrow the alert window",
        ) from None


@router.get("/track-record/calls", response_model=CallJournal)
@router.get("/track-record/calls/export", response_model=None)
def track_record_calls(
    request: Request,
    days: Annotated[int, Query(ge=1, le=365)] = 7,
    chain: Chain | None = None,
    stage: Stage | None = None,
    min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    max_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    outcome: Literal["positive", "negative", "flat", "unknown"] | None = None,
    revision: Annotated[str | None, Query(pattern=r"^(unknown|[0-9]{1,9})$")] = None,
    min_age_hours: Annotated[float | None, Query(ge=0, le=8760, allow_inf_nan=False)] = None,
    max_age_hours: Annotated[float | None, Query(ge=0, le=8760, allow_inf_nan=False)] = None,
    sort: Literal["newest", "age", "best", "worst", "mfe", "mae", "score"] = "newest",
    page: Page = 1,
    page_size: PageSize = 25,
    as_of: Cutoff = None,
) -> CallJournal | Response:
    from revival_radar.web.research_export import export_call_journal

    until = cutoff(request, as_of)
    if min_score is not None and max_score is not None and min_score > max_score:
        raise HTTPException(status_code=422, detail="min_score cannot exceed max_score")
    if min_age_hours is not None and max_age_hours is not None and min_age_hours > max_age_hours:
        raise HTTPException(status_code=422, detail="min_age_hours cannot exceed max_age_hours")
    filters = dict(
        chain=chain,
        stage=stage,
        min_score=min_score,
        max_score=max_score,
        outcome=outcome,
        revision=revision,
        min_age_hours=min_age_hours,
        max_age_hours=max_age_hours,
    )
    if not request.url.path.endswith("/export"):
        return read(
            request,
            as_of,
            ("track-record", "calls", days, *filters.values(), sort, page, page_size),
            lambda queries, at: queries.call_journal(at, days, filters, sort, page, page_size),
        )
    try:
        with public_reader(request.app.state.config) as queries:
            data = queries.call_journal(until, days, filters, sort, 1, page_size, everything=True)
        return Response(
            export_call_journal(data),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="saved-calls.csv"',
                "Cache-Control": "no-store",
            },
        )
    except sqlite3.Error:
        raise HTTPException(
            status_code=503,
            detail="Saved calls temporarily unavailable; retry or narrow the window",
        ) from None
