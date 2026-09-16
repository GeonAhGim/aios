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
    featureDisabled: "This feature is currently disabled.",
  },
  errors: {
    supportCode: "Support code: {{code}}",
    retryAfterSeconds: "Retry available in {{seconds}}s",
  },
  exchangePositions: {
    title: "Open positions",
    selectPrompt: "Select an exchange to see its open positions.",
    notFoundTitle: "No credential for this exchange.",
    empty: "No open positions on {{exchange}}.",
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
  research: {
    pageTitle: "Research data",
    kind: {
      filing: "Filing",
      news: "News",
      macro: "Macro",
      alt: "Alt data",
    },
    searchForm: {
      title: "Search",
      queryLabel: "Query",
      instrumentLabel: "Instrument (instrument_id)",
      asOfLabel: "As-of time (as_of)",
      run: "Search",
    },
    validation: {
      queryRequired: "Please enter a search query.",
    },
    results: {
      title: "Results",
      beforeRun: "Enter a query and search.",
      empty: "No items match the conditions.",
      truncated: "Results were truncated at the cap.",
      viewInChart: "View in chart",
      unmappedNoKey: "Unmapped (no deterministic key)",
      unmappedNotFound: "Unmapped (instrument not found)",
      totalLabel: "{{total}}",
    },
    sources: {
      title: "Source status",
      empty: "No sources registered.",
      redistribution: {
        storeFull: "Store full",
        storeExcerpt: "Store excerpt",
        linkOnly: "Link only",
      },
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
  notificationCenter: {
    title: "Notification center",
    filterLabel: "Channel",
    channel: {
      ALL: "All",
      EMAIL: "Email",
      PUSH: "Push",
      IN_APP: "In-app",
    },
    empty: "No notification history to show.",
    unknownDate: "Unknown date",
    totalCount: "{{count}}",
    stat: {
      total: "Total",
      sent: "Sent",
      failed: "Failed",
    },
  },
  commandPalette: {
    trigger: "Command palette",
    searchTitle: "Search commands",
    helpTitle: "Keyboard shortcuts",
    showHelp: "Show shortcuts",
    showSearch: "Back to search",
    placeholder: "Search by screen name...",
    empty: "No matching screens.",
    shortcut: {
      openSearch: "Open command palette",
      openHelp: "Open keyboard shortcuts help",
      navigate: "Move through the list",
      select: "Go to selected screen",
      close: "Close",
    },
  },
  onboarding: {
    pageTitle: "Get started",
    emptyStateCta: "Start onboarding",
    stepDone: "Done",
    stepUpcoming: "Upcoming",
    completeTitle: "You've completed your first run",
    completeDescription: "You connected an exchange, created a strategy, and ran a paper deployment.",
    goToDashboard: "Go to dashboard",
    steps: {
      connectExchange: {
        title: "Connect an exchange",
        description: "Connect an exchange account with a read-only API key.",
        cta: "Connect exchange",
      },
      createStrategy: {
        title: "Create a strategy",
        description: "Build conditions in the strategy builder and create your first strategy.",
        cta: "Create strategy",
      },
      runPaper: {
        title: "Run in paper mode",
        description: "Run your strategy against a sandbox account with no real funds.",
        cta: "Run paper deployment",
      },
    },
  },
  connectWizard: {
    pageTitle: "Connection wizard",
    stepLabel: "Step {{step}}/{{total}}",
    steps: {
      provider: {
        title: "1. Choose provider",
        providerLabel: "Provider code",
        keyLabel: "Account key (opaque_account_ref)",
        keyPlaceholder: "Paste the issued key",
        keyHint: "The key is stored only in the server KeyRing and is masked on screen.",
        capabilityLegend: "Requested permissions (read-only only)",
        next: "Next",
      },
      permissions: {
        title: "2. Review permissions",
        readonlyNotice: "All selected permissions are read-only. Order or withdrawal permissions are never requested.",
        confirmLabel: "I confirm only read-only permissions are requested.",
        back: "Back",
        next: "Next",
      },
      review: {
        title: "3. Review and connect",
        providerLabel: "Provider",
        keyLabel: "Account key",
        capabilityLabel: "Permissions",
        back: "Back",
        submit: "Connect",
        success: "Connection request sent. Check its status under Settings > Connections.",
        goToConnections: "Go to connections",
      },
    },
  },
  demoMode: {
    pageTitle: "Demo mode",
    description: "Try charts and backtests with a fixed sample dataset (1 year of 1-minute bars, 3 instruments) without a real connection.",
    instrumentListLabel: "Sample instruments",
    sampleTag: "Sample",
    startCta: "Start demo",
    chart: {
      pageTitle: "Demo chart",
      backLink: "Back to demo instruments",
      runBacktest: "Run backtest now",
      summary: {
        heading: "Backtest summary",
        finalEquity: "Final equity",
        trades: "Trades",
        maxDrawdown: "Max drawdown",
      },
      unknownInstrument: "Unknown demo instrument.",
    },
  },
  decisions: {
    pageTitle: "Decision history viewer",
    eventLineage: {
      heading: "Event lineage lookup (position_key)",
      label: "Position key (position_key)",
      button: "Look up",
      validationEmpty: "Enter a position key.",
    },
  },
  nav: {
    "/dashboard": "Dashboard",
    "/onboarding/first-run": "Get started",
    "/onboarding/connect": "Connection wizard",
    "/onboarding/demo": "Demo mode",
    "/exchanges": "Exchanges",
    "/market/instruments": "Instruments",
    "/market/candles": "Candles",
    "/chart": "Chart",
    "/screener": "Screener",
    "/research": "Research data",
    "/backtest/sweep-results": "Sweep results",
    "/strategy-builder": "Strategy builder",
    "/scripts/editor": "Script editor",
    "/marketplace": "Marketplace",
    "/follow": "Follow",
    "/ai/studio": "AI studio",
    "/executions": "Execution control",
    "/portfolio": "Portfolio",
    "/rebalance": "Rebalance",
    "/mandates": "Mandates",
    "/compliance": "Compliance",
    "/decisions/history": "Decision history",
    "/reports": "Reports",
    "/wallet": "Wallet",
    "/wallet/ledger": "Wallet ledger",
    "/wallet/payouts": "Payouts",
    "/alerts": "Alerts",
    "/notifications": "Notification center",
    "/system/paper-deployments": "Paper deployments",
    "/approval-requests": "Approval requests",
    "/settings/approval": "Approval settings",
    "/settings/notifications": "Notification settings",
    "/settings/sessions": "Sessions",
    "/settings/members": "Members",
    "/settings/account": "Delete account",
    "/settings/connections": "Connections",
    "/admin": "Admin",
    "/admin/system-status": "System status",
    "/admin/verification-queue": "Verification queue",
    "/admin/disputes": "Disputes",
    "/admin/users": "Users",
    "/admin/wallet-topups": "Wallet top-ups",
    "/admin/marketplace/platform-listings": "Platform listings",
    "/admin/approval-requests": "Approval requests",
    "/admin/safety-controls": "Safety controls",
    "/admin/reconciliation": "Reconciliation",
    "/admin/evidence-chain": "Evidence chain",
    "/admin/trust": "Trust membership",
  },
  legacy: { ...catalogEnLegacyA, ...catalogEnLegacyB, ...catalogEnLegacyC, ...catalogEnLegacyD },
} satisfies Stringify<CatalogKo>;

export type CatalogEn = typeof catalogEn;
