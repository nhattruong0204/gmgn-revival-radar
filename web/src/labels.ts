// Shared metric labels so pages cannot drift. Definitions: docs/performance-metrics.md.
export const labels = {
  marketCap: "Market cap",
  marketCapChange: "Market cap change since call",
  priceChange: "Price change",
  checkpointPriceChange: (hours: number | string) => `+${hours}h price change`,
  medianPriceChange: "Median observed price change",
  computablePriceChanges: "Computable price changes",
  checkpointNote:
    "Checkpoint values are price change since the alert price, not market-cap change.",
} as const;
