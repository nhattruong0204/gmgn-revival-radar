import pytest

from revival_radar.analysis.filters import first_pass
from revival_radar.clients.normalization import enriched_token, ranked_token
from revival_radar.models.token import TokenSnapshot

from .conftest import changed

GOOGL = "0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3"
SOL_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@pytest.fixture
def token(scenario):
    # Classification tests need an unknown identity, independent of demo mints.
    return TokenSnapshot(**(scenario["token"] | {"contract_address": "A" * 44}))


def test_reported_googl_is_excluded_without_metadata(token, config):
    stock = changed(token, chain="robinhood", contract_address=GOOGL, name="GOOGL", symbol="GOOGL")
    assert stock.asset_type == "tokenized_stock"
    assert "known contract: robinhood:" in stock.asset_classification_reason
    assert GOOGL in stock.asset_classification_reason
    assert first_pass(stock, config).reasons == ["asset_type: excluded tokenized_stock"]


@pytest.mark.parametrize("ticker", ["GOOGL", "TSLA", "AAPL", "USDT", "USDC", "WETH", "WSOL"])
def test_bare_symbol_and_name_collision_remains_eligible(token, config, ticker):
    meme = changed(token, name=ticker, symbol=ticker)
    assert meme.asset_type is None
    assert first_pass(meme, config).passed


@pytest.mark.parametrize(
    "name", ["Robinhood Rocket", "Google Meme", "Wrapped Cat", "Stable Genius"]
)
def test_robinhood_native_crypto_and_unknowns_are_kept(token, config, name):
    native = changed(token, chain="robinhood", contract_address="0x" + "a" * 40, name=name)
    assert native.asset_type is None
    assert first_pass(native, config).passed


@pytest.mark.parametrize(
    "asset_type,expected",
    [
        ("stablecoin", "stablecoin"),
        ("Wrapped Asset", "wrapped_asset"),
        ("tokenized-stock", "tokenized_stock"),
    ],
)
def test_explicit_metadata_is_normalized_and_excluded(token, config, asset_type, expected):
    parsed = enriched_token(token, {"asset_type": asset_type}, {})
    assert parsed.asset_type == expected
    assert parsed.asset_classification_reason == f"metadata: asset_type={expected}"
    assert first_pass(parsed, config).reasons == [f"asset_type: excluded {expected}"]


def test_explicit_native_metadata_cannot_bypass_known_identity(token, config):
    seed = changed(token, chain="robinhood", contract_address=GOOGL)
    parsed = enriched_token(seed, {"asset_type": "native_crypto"}, {})
    assert parsed.asset_type == "tokenized_stock"
    assert first_pass(parsed, config).reasons == ["asset_type: excluded tokenized_stock"]


def test_explicit_native_metadata_is_kept_for_unknown_identity(token, config):
    parsed = enriched_token(token, {"asset_type": "native_crypto"}, {})
    assert parsed.asset_type == "native_crypto"
    assert first_pass(parsed, config).passed


@pytest.mark.parametrize(
    "raw",
    [
        {"asset_type": "unsupported"},
        {"asset_type": {"name": "stablecoin"}},
        {"asset_type": ["stablecoin"]},
        {"type": "stablecoin", "category": "stocks", "tags": ["wrapped"]},
        {"is_stablecoin": True, "is_stock": True},
        {"pool": {"quote_symbol": "USDC"}},
        {"launchpad": "longxyz"},
        {"launchpad_platform": "some_stock_project"},
    ],
)
def test_ambiguous_or_unsupported_fields_do_not_exclude(token, config, raw):
    parsed = enriched_token(token, raw, {})
    assert parsed.asset_type is None
    assert first_pass(parsed, config).passed


@pytest.mark.parametrize(
    "chain,address,kind",
    [
        ("sol", SOL_USDC, "stablecoin"),
        ("sol", "So11111111111111111111111111111111111111112", "wrapped_asset"),
        ("base", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "stablecoin"),
        ("base", "0x4200000000000000000000000000000000000006", "wrapped_asset"),
        ("bsc", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", "wrapped_asset"),
    ],
)
def test_chain_qualified_known_contracts(token, config, chain, address, kind):
    known = changed(token, chain=chain, contract_address=address)
    assert known.asset_type == kind
    assert first_pass(known, config).reasons == [f"asset_type: excluded {kind}"]


def test_address_matching_is_evm_case_insensitive_and_chain_specific(token):
    checksummed = changed(token, chain="robinhood", contract_address="0x" + GOOGL[2:].upper())
    assert checksummed.asset_type == "tokenized_stock"
    same_address_other_chain = changed(token, chain="base", contract_address=GOOGL)
    assert same_address_other_chain.asset_type is None


def test_solana_mint_matching_preserves_case(token):
    different_mint = changed(token, contract_address="e" + SOL_USDC[1:])
    assert different_mint.asset_type is None
    assert different_mint.contract_address != SOL_USDC


def test_inferred_class_is_recomputed_after_identity_changes(token):
    wrapped = changed(token, contract_address="So11111111111111111111111111111111111111112")
    stock = changed(wrapped, chain="robinhood", contract_address=GOOGL)
    assert stock.asset_type == "tokenized_stock"
    unknown = changed(stock, contract_address="0x" + "c" * 40)
    assert unknown.asset_type is None
    assert unknown.asset_classification_reason is None


@pytest.mark.parametrize("label", ["native_crypto", "crypto", "meme", "unknown"])
def test_native_and_unknown_labels_cannot_bypass_stablecoin_identity(token, config, label):
    known = changed(token, contract_address=SOL_USDC)
    parsed = enriched_token(known, {"asset_type": label}, {})
    assert parsed.asset_type == "stablecoin"
    assert first_pass(parsed, config).reasons == ["asset_type: excluded stablecoin"]


@pytest.mark.parametrize(
    "kind,toggle",
    [
        ("tokenized_stock", "exclude_tokenized_stocks"),
        ("stablecoin", "exclude_stablecoins"),
        ("wrapped_asset", "exclude_wrapped_assets"),
    ],
)
def test_each_exclusion_can_be_disabled_without_disabling_data_gates(token, config, kind, toggle):
    classified = changed(token, asset_type=kind)
    disabled = config.model_copy(update={toggle: False})
    assert first_pass(classified, disabled).passed
    incomplete = changed(classified, liquidity=None)
    assert first_pass(incomplete, disabled).reasons == ["liquidity: unavailable"]
    assert first_pass(incomplete, config).reasons == [
        f"asset_type: excluded {kind}",
        "liquidity: unavailable",
    ]


@pytest.mark.parametrize(
    "chain,platform", [("sol", "xstocks"), ("bsc", "flap_stocks"), ("robinhood", "longxyz")]
)
def test_documented_stock_platform_in_rank_and_info(token, chain, platform):
    address = token.contract_address if chain == "sol" else "0x" + "b" * 40
    raw = {"address": address, "symbol": "EXAMPLE", "launchpad_platform": platform}
    ranked = ranked_token(raw, chain, "trending", 1, "1h", token.timestamp)
    assert ranked.asset_type == "tokenized_stock"
    assert ranked.asset_classification_reason == f"platform evidence: launchpad_platform={platform}"
    seed = changed(token, chain=chain, contract_address=address)
    enriched = enriched_token(seed, {"launchpad_platform": platform}, {})
    assert enriched.asset_type == "tokenized_stock"


def test_known_contract_precedes_platform_fallback(token):
    seed = changed(token, contract_address=SOL_USDC)
    parsed = enriched_token(seed, {"launchpad_platform": "xstocks"}, {})
    assert parsed.asset_type == "stablecoin"
    assert parsed.asset_classification_reason.startswith("known contract:")


@pytest.mark.parametrize(
    "name,kind",
    [
        ("Example Tokenized Stock", "tokenized_stock"),
        ("Example Tokenised Equity", "tokenized_stock"),
        ("Example USD Stablecoin", "stablecoin"),
        ("Wrapped Ether", "wrapped_asset"),
    ],
)
def test_narrow_name_fallback_records_heuristic_evidence(token, name, kind):
    parsed = enriched_token(token, {"name": name}, {})
    assert parsed.asset_type == kind
    assert parsed.asset_classification_reason.startswith("name evidence:")


def test_old_snapshot_deserializes_and_new_fields_stay_scalar(token):
    old = token.model_dump()
    old.pop("asset_type")
    old.pop("asset_classification_reason")
    restored = TokenSnapshot(**old)
    assert restored.asset_type is None
    assert restored.asset_classification_reason is None
    stock = changed(restored, chain="robinhood", contract_address=GOOGL)
    payload = stock.model_dump_json()
    reloaded = TokenSnapshot.model_validate_json(payload)
    assert reloaded.asset_type == stock.asset_type
    assert reloaded.asset_classification_reason == stock.asset_classification_reason
    assert isinstance(reloaded.asset_type, str)
    assert isinstance(reloaded.asset_classification_reason, str)


def test_explicit_rank_label_and_enrichment_preserve_evidence(token):
    ranked = ranked_token(
        {"address": token.contract_address, "asset_type": "stablecoin"},
        "sol",
        "hot_search",
        1,
        "1h",
        token.timestamp,
    )
    parsed = enriched_token(ranked, {"liquidity": 50_000}, {})
    assert parsed.asset_type == "stablecoin"
    assert parsed.asset_classification_reason == "metadata: asset_type=stablecoin"
