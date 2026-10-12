# Helius Solana mint verification

The live data source combines GMGN market analytics with Helius Solana RPC. It uses
the user-provided `HELIUS_API_TOKEN` only with `https://mainnet.helius-rpc.com/`.
Requests are read-only `getAccountInfo`, with `encoding=jsonParsed` and
`commitment=confirmed`. Redirects are disabled. There are no transaction, account
creation, wallet, webhook, or trading operations.

## Data and validation

The adapter validates the JSON-RPC response identity, mint address, token program,
parsed mint type, initialization, explicit nullable authority fields, integer slot,
u64 raw supply, and decimals. A missing authority field is unknown, rather than
revoked. Both supported token programs follow the current official interface IDs.
The [Helius RPC reference](https://www.helius.dev/docs/api-reference/rpc/http/getaccountinfo),
[Solana RPC reference](https://solana.com/docs/rpc/http/getaccountinfo),
[Token-2022 interface](https://raw.githubusercontent.com/solana-program/token-2022/main/interface/src/lib.rs),
and [Agave parsed token types](https://raw.githubusercontent.com/anza-xyz/agave/master/account-decoder-client-types/src/token.rs)
are the primary references.

Evidence lives in `Security.solana_mint`: provider, requested mint, receipt time,
confirmed slot, program, raw total supply, decimals, mint/freeze revocation, and
extension names. The slot is not a wall-clock timestamp and confirmed commitment
does not mean finalized. Supply is a decimal integer string in raw token units.
It does not replace circulating supply, current market cap, or historical ATH.
Prices, volume/TX windows, holders, concentration labels, liquidity, candles, and
discovery remain sourced from GMGN. This integration improves authority evidence;
it does not by itself remove market-data requests or add new discovery coverage.

## Risk and availability

Checks occur after the current market gate, including candidates still collecting
activity history. A positive active mint/freeze authority finding sets `dangerous`
and prevents alerts before expensive candle/security work. Independent Helius
evidence is reapplied after GMGN security fetches and cache merges, while the GMGN
security cache remains separate. A revoked authority cannot clear an existing
GMGN risk. Conflicting authority reports are disclosed rather than silently mixed.

Token-2022 extension names are retained without interpreting their states. This
version does not assess delegate powers, hooks, fees, frozen defaults, pausing, or
other extension behavior. These limitations are visible in saved signal warnings.
Neither successful authority checks nor absent extensions establish token safety.

Helius is supplemental. A failed, expired, malformed, or budget-deferred check adds
an unverified warning and preserves the existing GMGN scoring/eligibility policy.
Fresh market history continues to be collected; failures are not cached as successful
evidence. A Helius outage therefore does not become an independent alert prerequisite.

## Operational settings

| Setting | Default | Purpose |
|---|---:|---|
| `HELIUS_API_TOKEN` | empty | Private RPC credential; absent means inactive |
| `HELIUS_ENABLED` | true | Startup switch; also requires a token |
| `HELIUS_CACHE_TTL_SECONDS` | 900 | Maximum evidence age; zero disables cache |
| `MAX_HELIUS_VERIFY_PER_SCAN` | 20 | New logical checks, including failures |
| `HELIUS_SCAN_BUDGET_SECONDS` | 15 | Cumulative request time per scan |
| `HELIUS_REQUEST_SPACING_SECONDS` | 0.12 | Independent request pacing |
| `HELIUS_HTTP_TIMEOUT_SECONDS` | 10 | Per-attempt timeout |
| `HELIUS_HTTP_ATTEMPTS` | 2 | Maximum attempts per logical check |
| `HELIUS_RETRY_MAX_WAIT_SECONDS` | 2 | Maximum in-request retry wait |

The cache namespace versions provider, method policy and commitment. Cache hits
retain original receipt time/expiry; identity, future dates, age, and payload types
are revalidated. Cache hits do not consume the new-check budget. GMGN pacing and
market/security/candle budgets are unaffected. Long cooldowns stop Helius HTTP
attempts while leaving GMGN requests available. Logical deferrals and actual HTTP
attempts are counted separately in `/health` Performance and persisted scan metrics.

Authentication rejection cools down for 300 seconds; exhausted transport/server
failures use at least 30 seconds. HTTP/JSON-RPC 429 defaults to 60 seconds and
honors server retry headers. Supported RPC node-outage errors activate a
protective cooldown without being misclassified as HTTP 429. Error text is locally
authored and excludes response bodies, URLs, and credentials.

Helius [documents standard RPC calls at one credit](https://www.helius.dev/docs/billing/credits).
At the default 20 checks and two attempts, a theoretical maximum is 40 HTTP attempts
per scan before the time limit, caching or cooldowns reduce that count. This is an
implementation bound, not a promise about account cost or remaining quota.

No database migration is needed: the existing nested security JSON and independent
enrichment-cache rows store these fields. Older snapshots remain readable. The
nonsecret provider mode and budget settings are recorded with each configuration
so analysis can distinguish Helius-enabled observations from earlier cohorts.
