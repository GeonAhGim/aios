// Grouped sub-barrels under ./index/* keep each file under the P6 300-line
// ratchet (frontend-file-size-baseline.json). Consumers must keep importing
// from "@aios/chart-engine" (or deep "@aios/chart-engine/src/**" paths) —
// this file's re-export surface is unchanged, only its internals moved.
export * from "./index/core";
export * from "./index/drawings";
export * from "./index/data";
export * from "./index/layout";
export * from "./index/compare";
export * from "./index/panes";
export * from "./index/legend";
export * from "./index/templates";
export * from "./index/render";
export * from "./index/indicators";
export * from "./index/compute";
