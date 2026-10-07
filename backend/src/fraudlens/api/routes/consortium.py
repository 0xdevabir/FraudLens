consortium.py 120L cognitive
// /Users/mdabirhossain/Documents/WebDevelopment/FraudLens/backend/src/fraudlens/api/routes/consortium.py
§ function service(p (L30-L39)
def service(p: Plat) -> ConsortiumService:
    key = str(p.settings.artifacts_dir)
    with _lock:
        # Reload when missing so placing artifacts (or Retry) works without a process restart.
        if _services.get(key) is None:
            _services[key] = ConsortiumService.load(p.settings.artifacts_dir)
    svc = _services[key]
    if svc is None:
        raise WorkflowError(404, "consortium_not_built",
                            "run `python -m fraudlens.consortium.simulate` first")  # fmt: skip
    return svc
// ... 21 lines omitted
§ function overview(p (L61-L63)
def overview(p: Plat, ctx: Reviewer) -> dict:
    """Members, their latest signed feeds, the privacy guarantees and the measured results."""
    return clean(service(p).overview())
// ... 3 lines omitted
§ function matches(p (L67-L70)
def matches(p: Plat, ctx: Reviewer,
            limit: Annotated[int, Query(ge=1, le=500)] = 200) -> list[dict]:  # fmt: skip
    """Test-period receivers that matched a partner listing (one row per wallet)."""
    return clean(service(p).matches(limit))
// ... 3 lines omitted
§ function hub_audit(p (L74-L77)
def hub_audit(p: Plat, ctx: Reviewer,
              limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict]:  # fmt: skip
    """The hub's hash-chained log, newest first: counts and ids, never identifiers."""
    return clean(service(p).audit(limit))
// ... 3 lines omitted
§ function lookup(body (L81-L90)
def lookup(body: Lookup, p: Plat, s: Db, ctx: Reviewer) -> dict:
    svc = service(p)
    try:
        out = svc.lookup(body.wallet_id)
    except ProtocolError as exc:
        raise WorkflowError(404, "not_in_consortium", str(exc)) from exc
    audit(s, ctx, "consortium.lookup", "wallet", body.wallet_id,
          matches=len(out["matches"]), signal=out["signal"])  # fmt: skip
    s.commit()
    return clean(out)
// ... 3 lines omitted
§ function open_dispute(body (L94-L102)
def open_dispute(body: DisputeIn, p: Plat, s: Db, ctx: Reviewer) -> dict:
    """Contest a listing: it stops counting for every member at once."""
    try:
        out = service(p).open_dispute(body.raised_by, body.listing_id, body.reason)
    except ProtocolError as exc:
        raise _protocol(exc) from exc
    audit(s, ctx, "consortium.dispute", "listing", body.listing_id, dispute=out["dispute_id"])
    s.commit()
    return clean(out)
// ... 3 lines omitted
§ function resolve (L106-L120)
def resolve(
    dispute_id: Annotated[str, Path(pattern=r"^D\d{4}$")],
    body: Resolve,
    p: Plat,
    s: Db,
    ctx: Supervisor,
) -> dict:
    """Decide a dispute for the listing member: keep the listing, or withdraw it for good."""
    try:
        out = service(p).resolve_dispute(dispute_id, body.outcome)
    except ProtocolError as exc:
        raise _protocol(exc) from exc
    audit(s, ctx, "consortium.resolve", "dispute", dispute_id, outcome=body.outcome)
    s.commit()
    return clean(out)
7/18 chunks shown (647 tokens)
[lean-ctx] full source: read "/Users/mdabirhossain/Documents/WebDevelopment/FraudLens/backend/src/fraudlens/api/routes/consortium.py" directly (no MCP)  ·  or ctx_read("/Users/mdabirhossain/Documents/WebDevelopment/FraudLens/backend/src/fraudlens/api/routes/consortium.py", mode="full")
