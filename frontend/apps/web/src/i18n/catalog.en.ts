// task-2686 (UX-2): first-draft English catalog. Mirrors catalog.ko key-for-key
// (enforced by `satisfies CatalogKo` below -- a missing/renamed key fails tsc, not
// just a runtime fallback) so switching languages never silently falls back to a
// missing key. The `common`/`errors`/`ai` sections translate UX-1's hand-written
// catalog.ko; `legacy` mirrors the mechanically extracted per-file namespaces
// (scripts/i18n-literals-baseline.json) -- both are a rough draft, not a reviewed
// translation.
import type { CatalogKo } from "./catalog.ko";
import { catalogEnLegacyA } from "./catalog.en.legacyA";
import { catalogEnLegacyB } from "./catalog.en.legacyB";
import { catalogEnLegacyC } from "./catalog.en.legacyC";
import { catalogEnLegacyD } from "./catalog.en.legacyD";

// catalog.ko's values are `as const` literal strings (react-i18next needs that for
// interpolation-argument type inference off the ko resource). catalogEn only needs
// to cover the same key paths with (any) string values -- not the same literal
// Korean text -- so we check shape via a literal-to-string mapped type instead of
// `satisfies CatalogKo` directly.
type Stringify<T> = T extends string ? string : { [K in keyof T]: Stringify<T[K]> };

export const catalogEn = {
  common: {
    confirm: "Confirm",
    cancel: "Cancel",
    retry: "Retry",
    save: "Save",
    loading: "Loading",
  },
  errors: {
    supportCode: "Support code: {{code}}",
    retryAfterSeconds: "Retry available in {{seconds}}s",
  },
  ai: {
    pageTitle: "AI Research Studio",
    sections: {
      providers: "Provider settings",
      tokens: "Agent tokens",
      proposals: "Proposals",
      experiments: "Experiment comparison",
    },
    providers: {
      name: {
        anthropic: "Anthropic",
        openaiCompatible: "OpenAI-compatible (including local LLMs)",
        gemini: "Google Gemini",
        externalAgent: "External agent (Claude Code, Codex CLI, etc.)",
      },
      enabledLabel: "Enabled",
      dailyBudgetLabel: "Daily budget cap (USD)",
      empty: "No providers registered.",
    },
    tokens: {
      issueTitle: "Issue new token",
      scope: {
        read: "read (metrics/data lookup)",
        research: "research (backtest/sweep execution)",
        propose: "propose (submit proposal)",
        paper: "paper (PAPER execution request)",
      },
      instrumentsLabel: "Allowed instruments (comma-separated, empty = all)",
      notionalCapLabel: "Notional cap",
      expiresAtLabel: "Expires at (ISO 8601)",
      issue: "Issue",
      revoke: "Revoke",
      expiresPrefix: "Expires",
      empty: "No tokens issued.",
    },
    proposals: {
      empty: "No proposals.",
      providerPrefix: "Provider",
      promote: "Promote to PAPER",
      disclaimer:
        "Proposals must pass the compile/validation/risk gates in code, and only PASS proposals can be promoted to PAPER.",
    },
    promote: {
      confirmTitle: "Confirm promotion to PAPER",
      digestLabel: "Confirmation digest",
      expiresPrefix: "Expires",
    },
    experiments: {
      compareA: "Comparison experiment A",
      compareB: "Comparison experiment B",
      metricColumn: "Metric",
      empty: "No experiments.",
      noMetrics: "No metrics to compare.",
    },
  },
  screener: {
    pageTitle: "Screener",
    filterBuilder: {
      title: "Filter builder",
      universeLabel: "Universe",
      universePlaceholder: "e.g. KRX, BITGET",
      kindLabel: "Kind",
      kind: {
        indicator: "Indicator",
        fundamental: "Fundamental",
        research: "Research",
        backtest_stat: "Backtest stat",
      },
      fieldLabel: "Field",
      operatorLabel: "Operator",
      valueLabel: "Value",
      asOfLabel: "As-of time (as_of)",
      addFilter: "Add filter",
      removeFilter: "Remove filter",
      sortFieldLabel: "Sort field",
      sortDirection: {
        asc: "Ascending",
        desc: "Descending",
      },
      run: "Run",
    },
    validation: {
      universeRequired: "Please enter a universe.",
      filtersRequired: "Add at least one filter.",
      filterIncomplete: "Fill in the field/value for filter #{{position}}.",
      filterResearchAsOfRequired: "Research filter #{{position}} requires an as-of time (as_of) to prevent leakage.",
    },
    results: {
      title: "Results",
      beforeRun: "Configure filters and run.",
      empty: "No instruments match the conditions.",
      truncated: "Results were truncated at the 1,000-row cap.",
      viewInChart: "View in chart/backtest",
      totalLabel: "{{total}}",
    },
  },
  whatif: {
    order: {
      title: "What-if order impact preview",
      symbolLabel: "Symbol",
      sideLabel: "Side",
      side: {
        buy: "Buy",
        sell: "Sell",
      },
      quantityLabel: "Quantity",
      run: "Preview",
      beforeRun: "Enter an order and run the preview.",
    },
    orderValidation: {
      symbolRequired: "Please enter a symbol.",
      quantityInvalid: "Quantity must be a number greater than 0.",
    },
    impact: {
      exposureDeltaLabel: "Exposure change",
      concentrationDeltaLabel: "Concentration change",
      varDeltaLabel: "VaR change",
      limitHeadroomDeltaLabel: "Limit headroom change",
    },
    rebalance: {
      pageTitle: "Rebalance",
      targetBuilderTitle: "Target weights",
      symbolLabel: "Symbol",
      weightLabel: "Target weight (%)",
      addTarget: "Add target",
      removeTarget: "Remove",
      run: "Generate plan",
      resultsTitle: "Plan",
      beforeRun: "Configure target weights and generate a plan.",
      resultsEmpty: "No trades generated.",
      turnoverLabel: "Turnover {{value}}%",
      estCostLabel: "Est. cost {{value}}",
      skippedTitle: "Skipped",
      previewImpact: "Preview impact",
    },
    targetValidation: {
      targetsRequired: "Add at least one target weight.",
      targetSymbolRequired: "Please enter a symbol for target #{{position}}.",
      targetWeightInvalid: "Target #{{position}}'s weight must be a number between 0 and 100.",
      targetSymbolDuplicate: "Symbol for target #{{position}} is duplicated.",
    },
  },
  legacy: { ...catalogEnLegacyA, ...catalogEnLegacyB, ...catalogEnLegacyC, ...catalogEnLegacyD },
} satisfies Stringify<CatalogKo>;

export type CatalogEn = typeof catalogEn;
