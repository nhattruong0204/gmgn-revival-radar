"""Verbatim pre-#100 PublicQueries.radar (called with a PublicQueries as ``self``)."""

from revival_radar.web.models import RadarPage


def legacy_radar(
    self,
    until: float,
    *,
    chain: str | None,
    status: str | None,
    search: str,
    min_score: int,
    include_ignore: bool,
    sort: str,
    order: str,
    page: int,
    page_size: int,
    age_hours: int = 24,
) -> RadarPage:
    filters, arguments = ["e.score>=?"], [min_score]
    if status:
        filters.append("e.status=?")
        arguments.append(status)
    elif not include_ignore:
        filters.append("e.status!='IGNORE'")
    if chain:
        filters.append("e.chain=?")
        arguments.append(chain)
    if search:
        # LIKE escapes treat user input literally; mint address case remains significant.
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        # OR short-circuits left to right: cheap column tests first, so the
        # payload-validating name lookup only runs for rows still undecided.
        filters.append(
            "(instr(e.contract_address,?)>0 OR e.symbol LIKE ? ESCAPE '\\' OR "
            "public_field(st.value,e.chain,e.contract_address,e.timestamp,e.score,"
            "e.status,e.eligible,'name') LIKE ? ESCAPE '\\')"
        )
        arguments.extend((search, f"%{escaped}%", f"%{escaped}%"))
    # Latest completed evaluation per token in one ranked pass. Walking chains
    # with a loose index scan keeps the window range-bound on the covering
    # (chain,timestamp) index instead of probing every identity separately.
    # A chain filter replaces the walk: ranking is partitioned by chain anyway.
    walk = (
        "SELECT ?"
        if chain
        else "SELECT MIN(chain) FROM evaluations UNION ALL SELECT (SELECT MIN(chain) "
        "FROM evaluations WHERE chain>c) FROM chains WHERE c IS NOT NULL"
    )
    cte = f"""WITH RECURSIVE chains(c) AS ({walk}
    ), ranked AS (
        SELECT w.id,ROW_NUMBER() OVER (PARTITION BY w.chain,w.contract_address
            ORDER BY w.timestamp DESC,w.id DESC) AS rn
        FROM chains c CROSS JOIN evaluations w JOIN scan_runs s ON s.id=w.scan_id
        WHERE w.chain=c.c AND w.timestamp>? AND w.timestamp<=?
        AND s.finished IS NOT NULL AND s.finished<=?
    ), latest AS (
        SELECT e.* FROM ranked r JOIN evaluations e ON e.id=r.id WHERE r.rn=1
    ) """
    selection = (
        "FROM latest e LEFT JOIN state st ON st.key='telegram:evaluation:' || e.id WHERE "
        + " AND ".join(filters)
    )
    parameters = (
        *((chain,) if chain else ()),
        until - age_hours * 3600,
        until,
        until,
        *arguments,
    )
    if sort in ("score", "timestamp"):
        sorting = f"e.{sort}"
    else:
        # sort is an enum at the HTTP boundary and additionally checked here.
        if sort not in ("market_cap", "liquidity", "volume_5m"):
            raise ValueError("Unsupported sort")
        sorting = (
            "public_field(st.value,e.chain,e.contract_address,e.timestamp,e.score,"
            f"e.status,e.eligible,'{sort}')"
        )
    if order not in ("asc", "desc"):
        raise ValueError("Unsupported order")
    # The total comes from the same ranked pass as the page; only a page past
    # the end (no rows to carry it) needs a separate count.
    found = self.db.execute(
        cte
        + "SELECT e.*,COUNT(*) OVER () AS total_matches "
        + selection
        + f" ORDER BY ({sorting}) IS NULL ASC,({sorting}) {order.upper()},"
        "e.timestamp DESC,e.chain,e.contract_address LIMIT ? OFFSET ?",
        (*parameters, page_size, (page - 1) * page_size),
    ).fetchall()
    if found:
        total = found[0]["total_matches"]
    elif page == 1:
        total = 0
    else:
        total = self.db.execute(cte + "SELECT COUNT(*) " + selection, parameters).fetchone()[0]
    rows = found
    return RadarPage(
        items=[self._evaluation(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
        as_of=until,
        last_scan_at=self.last_scan_at(until),
    )
