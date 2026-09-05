/**
 * The only module under `src/` that imports from `vendor/klinecharts`.
 * Everything the wrappers need from the fork is re-exported here, so a vendor
 * upgrade (or replacement) is a one-file change and the rest of the package
 * never learns vendor paths. Vendor sources are byte-identical to upstream
 * v10.0.3 — see NOTICE-AIOS.md for provenance and the (empty) modified-file list.
 */

export {
  dispose as disposeVendorChart,
  getSupportedIndicators,
  init as initVendorChart,
  registerIndicator,
} from "../../vendor/klinecharts/src/index";
export type { Chart as VendorChart } from "../../vendor/klinecharts/src/index";
export type { KLineData } from "../../vendor/klinecharts/src/common/Data";
export type { DataLoader } from "../../vendor/klinecharts/src/common/DataLoader";
export type { Indicator, IndicatorTemplate } from "../../vendor/klinecharts/src/component/Indicator";
export { PaneIdConstants } from "../../vendor/klinecharts/src/pane/types";

/**
 * Upstream tag the fork was taken from. The vendor's own `version()` returns
 * the build-time placeholder `__VERSION__` because we ship source, not dist.
 */
export const KLINECHARTS_VENDOR_VERSION = "10.0.3";
