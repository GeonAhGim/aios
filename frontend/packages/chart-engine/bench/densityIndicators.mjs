/**
 * CH-19b — the bench's 30 indicator instances, split out of
 * density_bench.mjs to keep that file under the 300-line budget. Only 10
 * kernels are verified for client-side compute (CH-18a
 * `verifiedIndicators.ts`), so the 30 instances are modeled as 3 parameter
 * variants per kernel (e.g. SMA(9)/SMA(20)/SMA(50) plotted side by side) —
 * a realistic chart-usage pattern, and honest about what actually runs
 * client-side, unlike fabricating 30 distinct algorithm names.
 */

const KERNEL_INPUTS = {
  SMA: ["close"], EMA: ["close"], RSI: ["close"],
  ATR: ["high", "low", "close"], CCI: ["high", "low", "close"],
  WILLR: ["high", "low", "close"], MFI: ["high", "low", "close", "volume"],
  MACD: ["close"], STOCH: ["high", "low", "close"], OBV: ["close", "volume"],
};
const KERNEL_OUTPUTS = {
  SMA: ["value"], EMA: ["value"], RSI: ["value"], ATR: ["value"], CCI: ["value"],
  WILLR: ["value"], MFI: ["value"], OBV: ["value"],
  MACD: ["macd", "signal", "hist"], STOCH: ["slowk", "slowd"],
};
/** 10 kernels x 3 param variants = 30 instances. */
const PARAM_VARIANTS = {
  SMA: [{ timeperiod: 9 }, { timeperiod: 20 }, { timeperiod: 50 }],
  EMA: [{ timeperiod: 9 }, { timeperiod: 20 }, { timeperiod: 50 }],
  RSI: [{ timeperiod: 7 }, { timeperiod: 14 }, { timeperiod: 21 }],
  ATR: [{ timeperiod: 7 }, { timeperiod: 14 }, { timeperiod: 21 }],
  CCI: [{ timeperiod: 7 }, { timeperiod: 14 }, { timeperiod: 20 }],
  WILLR: [{ timeperiod: 7 }, { timeperiod: 14 }, { timeperiod: 21 }],
  MFI: [{ timeperiod: 7 }, { timeperiod: 14 }, { timeperiod: 21 }],
  MACD: [
    { fastperiod: 12, slowperiod: 26, signalperiod: 9 },
    { fastperiod: 5, slowperiod: 35, signalperiod: 5 },
    { fastperiod: 19, slowperiod: 39, signalperiod: 9 },
  ],
  STOCH: [
    { fastk_period: 5, slowk_period: 3, slowd_period: 3 },
    { fastk_period: 14, slowk_period: 3, slowd_period: 3 },
    { fastk_period: 21, slowk_period: 5, slowd_period: 5 },
  ],
  OBV: [{}, {}, {}], // OBV takes no params; 3 instances still model 3 concurrent plotted lines.
};

export function buildCatalog(verifiedKernelPins) {
  return Object.keys(verifiedKernelPins).map((name) => {
    const pin = verifiedKernelPins[name];
    return {
      name, tier: pin.tier, category: "bench", version: "bench", hash: pin.entryHash,
      inputs: KERNEL_INPUTS[name], outputs: KERNEL_OUTPUTS[name],
    };
  });
}

export function buildInstances(catalog, createClientIncrementalIndicator) {
  const instances = [];
  for (const name of Object.keys(PARAM_VARIANTS)) {
    for (const params of PARAM_VARIANTS[name]) {
      instances.push({
        name,
        indicator: createClientIncrementalIndicator(name, params, catalog),
        primaryOutput: KERNEL_OUTPUTS[name][0],
        scale: name === "SMA" || name === "EMA" ? "overlay" : "own",
      });
    }
  }
  return instances;
}
