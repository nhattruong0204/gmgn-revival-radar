from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class ChainConfig:
    id: str
    display_name: str
    explorer_base_url: str | None

    @property
    def gmgn_id(self) -> str:
        return self.id

    def links(self, address: str) -> dict[str, str]:
        encoded = quote(address, safe="")
        links = {"GMGN": f"https://gmgn.ai/{self.id}/token/{encoded}"}
        if self.explorer_base_url:
            links["Explorer"] = f"{self.explorer_base_url}/{encoded}"
        return links


# New-chain explorer networks are not guessed; add a verified URL when available.
CHAINS = {
    "sol": ChainConfig("sol", "Solana", "https://solscan.io/token"),
    "bsc": ChainConfig("bsc", "BNB Smart Chain", "https://bscscan.com/token"),
    "base": ChainConfig("base", "Base", "https://basescan.org/token"),
    "robinhood": ChainConfig("robinhood", "Robinhood Chain", None),
    "arc": ChainConfig("arc", "Arc", None),
}
