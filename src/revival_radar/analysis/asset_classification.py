"""Conservative exclusion hints, not a complete or verified asset taxonomy.

GMGN's documented rank/info schema does not guarantee an asset-class field.
Recognized explicit labels are accepted when supplied; otherwise a small chain
and contract registry, exact stock-platform labels, and narrow full-name clues
are fallbacks. Names/platforms can be self-declared. Unknown assets remain
eligible for the usual filters; neither a ticker nor a chain proves asset type.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AssetClassification:
    asset_type: str | None = None
    reason: str | None = None


_LABELS = {
    "tokenized_stock": "tokenized_stock",
    "tokenized_stocks": "tokenized_stock",
    "tokenised_stock": "tokenized_stock",
    "tokenized_equity": "tokenized_stock",
    "stock": "tokenized_stock",
    "equity": "tokenized_stock",
    "stablecoin": "stablecoin",
    "stable_coin": "stablecoin",
    "wrapped_asset": "wrapped_asset",
    "wrapped_token": "wrapped_asset",
    "wrapped": "wrapped_asset",
    "native_crypto": "native_crypto",
    "cryptocurrency": "native_crypto",
    "crypto": "native_crypto",
    "meme": "native_crypto",
    "memecoin": "native_crypto",
}

# Deliberately small registry. Addresses are chain-specific; Solana mints are
# case-sensitive. The Robinhood GOOGL identity comes from the reported alert.
_KNOWN_CONTRACTS = {
    ("robinhood", "0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3"): (
        "tokenized_stock",
        "reported GOOGL stock token",
    ),
    ("sol", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"): ("stablecoin", "USDC"),
    ("sol", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"): ("stablecoin", "USDT"),
    ("sol", "So11111111111111111111111111111111111111112"): ("wrapped_asset", "wrapped SOL"),
    ("base", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"): ("stablecoin", "USDC"),
    ("base", "0x4200000000000000000000000000000000000006"): ("wrapped_asset", "wrapped ETH"),
    ("bsc", "0x55d398326f99059ff775485246999027b3197955"): ("stablecoin", "Binance-Peg USDT"),
    ("bsc", "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"): ("stablecoin", "Binance-Peg USDC"),
    ("bsc", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"): ("wrapped_asset", "wrapped BNB"),
}

# Documented launchpad_platform values, not generic launchpad/free-text tags.
# See gmgn-token/SKILL.md (info fields), gmgn-market/SKILL.md (rank fields),
# and gmgn-dev-score/references/fields.md (Robinhood longxyz stock example).
_STOCK_PLATFORMS = {
    ("sol", "xstocks"),
    ("bsc", "flap_stocks"),
    ("robinhood", "flap_stocks"),
    ("robinhood", "longxyz"),
}


def explicit_asset_type(value: object) -> str | None:
    """Accept only an explicit, recognized class label; never coerce objects."""
    if not isinstance(value, str):
        return None
    label = re.sub(r"[\s-]+", "_", value.strip().casefold())
    return _LABELS.get(label)


def classify_asset(
    *,
    chain: str,
    contract_address: str,
    name: str = "",
    asset_type: str | None = None,
    asset_classification_reason: str | None = None,
    launchpad_platform: object = None,
) -> AssetClassification:
    """Use exclusion labels/known identities before weaker platform/name clues.

    Explicit native-crypto labels never override a curated excluded identity.
    Recompute fallback classifications when a snapshot's identity/name changes.
    """
    explicit = explicit_asset_type(asset_type)
    reason = asset_classification_reason or ""
    fallback_reason = reason.startswith(("known contract:", "platform evidence:", "name evidence:"))
    if explicit in {"tokenized_stock", "stablecoin", "wrapped_asset"} and not fallback_reason:
        return AssetClassification(
            explicit, asset_classification_reason or f"metadata: asset_type={explicit}"
        )

    address = contract_address if isinstance(contract_address, str) else ""
    if chain != "sol":
        address = address.lower()
    known = _KNOWN_CONTRACTS.get((chain, address))
    if known is not None:
        return AssetClassification(known[0], f"known contract: {chain}:{address} ({known[1]})")

    # A self-declared crypto/meme label cannot bypass a curated contract match.
    if explicit == "native_crypto" and not fallback_reason:
        return AssetClassification(
            explicit, asset_classification_reason or f"metadata: asset_type={explicit}"
        )

    platform = launchpad_platform.casefold().strip() if isinstance(launchpad_platform, str) else ""
    # Preserve documented platform evidence across snapshot round trips without
    # promoting the previously inferred class above an exact contract identity.
    if not platform and reason.startswith("platform evidence: launchpad_platform="):
        platform = reason.removeprefix("platform evidence: launchpad_platform=")
    if (chain, platform) in _STOCK_PLATFORMS:
        return AssetClassification(
            "tokenized_stock", f"platform evidence: launchpad_platform={platform}"
        )

    normalized_name = " ".join(name.casefold().split()) if isinstance(name, str) else ""
    if re.search(r"\btokeni[sz]ed (?:stock|equity)(?:\b|s\b)", normalized_name):
        return AssetClassification("tokenized_stock", "name evidence: tokenized stock/equity")
    if re.fullmatch(r"(?:[a-z0-9 .'-]+ )?stablecoin", normalized_name):
        return AssetClassification("stablecoin", "name evidence: explicitly named stablecoin")
    if normalized_name in {
        "wrapped ether",
        "wrapped ethereum",
        "wrapped bitcoin",
        "wrapped sol",
        "wrapped solana",
        "wrapped bnb",
    }:
        return AssetClassification("wrapped_asset", "name evidence: wrapped underlying asset")

    return AssetClassification()
