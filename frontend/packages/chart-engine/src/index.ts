export type { CreateRendererOptions, RenderSize, Renderer, RendererBackend } from "./core/renderer";
export { createNullRendererBackend, createRenderer } from "./core/renderer";

export type { CreateTimeScaleOptions, TimeRange, TimeScale } from "./core/timeScale";
export { createTimeScale } from "./core/timeScale";

export type { CreatePriceScaleOptions, PriceRange, PriceScale } from "./core/priceScale";
export { createPriceScale } from "./core/priceScale";

export type {
  CandlePoint,
  ChartEngine,
  CreateChartEngineOptions,
  SeriesBackend,
  SeriesBackendFactory,
  SeriesHandle,
  SeriesOptions,
  SeriesType,
} from "./core/series";
export { createChartEngine, createNullSeriesBackendFactory } from "./core/series";

export type {
  OverlayDefinition,
  OverlayEntry,
  OverlayOutput,
  OverlayPlacement,
  OverlayRegistry,
  OverlayRegistryErrorCode,
} from "./indicators/overlayRegistry";
export {
  DEFAULT_OVERLAY_DEFINITIONS,
  INDICATOR_REGISTRY_VERSION,
  MAIN_PANE_INDEX,
  OverlayRegistryError,
  createDefaultOverlayRegistry,
  createOverlayRegistry,
} from "./indicators/overlayRegistry";

export type {
  Drawing,
  DrawingCollection,
  DrawingCoordinateSystem,
  DrawingDelta,
  DrawingErrorCode,
  DrawingKind,
  DrawingPoint,
  DrawingStyle,
  FibonacciDrawing,
  HorizontalLineDrawing,
  PixelPoint,
  RectangleDrawing,
  TrendLineDrawing,
  TwoPointDrawing,
  VerticalLineDrawing,
} from "./drawings/model";
export {
  DEFAULT_FIBONACCI_LEVELS,
  DRAWING_KINDS,
  DrawingError,
  assertValidDrawing,
  fromPixel,
  hasPoints,
  isDrawingKind,
  toPixel,
} from "./drawings/model";

export type { DrawingOptions, FibonacciLevelPrice, RectangleBounds } from "./drawings/tools";
export {
  addDrawing,
  anchorCount,
  createFibonacci,
  createHorizontalLine,
  createRectangle,
  createTrendLine,
  createVerticalLine,
  fibonacciLevelPrices,
  findDrawing,
  moveAnchor,
  moveDrawing,
  rectangleBounds,
  removeDrawing,
  setLocked,
  trendLineSlope,
  updateDrawing,
} from "./drawings/tools";

export type { DrawingsDocument } from "./drawings/serialize";
export {
  DRAWINGS_SCHEMA_VERSION,
  deserializeDrawings,
  fromDrawingsDocument,
  serializeDrawings,
  toDrawingsDocument,
} from "./drawings/serialize";

export type {
  ApplyResult,
  CandleStream,
  CandleStreamRejection,
  CandleStreamRejectionCode,
  CandleStreamSnapshot,
  CreateCandleStreamOptions,
  GapMarker,
  RealtimeCandleSource,
  RealtimeCandleUpdate,
  StreamCandle,
} from "./data/candleStream";
export { createCandleStream } from "./data/candleStream";

export type { TimeScaleBackend } from "./core/timeScale";
export type { PriceScaleBackend } from "./core/priceScale";

export type {
  CreateKlinechartsChartEngineOptions,
  KlinechartsBackend,
  KlinechartsChartEngine,
} from "./core/klinechartsBackend";
export { createKlinechartsBackend, createKlinechartsChartEngine } from "./core/klinechartsBackend";
export type { VendorChart } from "./core/klinecharts";
export { KLINECHARTS_VENDOR_VERSION } from "./core/klinecharts";
export { AIOS_HISTOGRAM_INDICATOR, AIOS_LINE_INDICATOR } from "./core/klinechartsSeries";

export type {
  CreateReplayControllerOptions,
  ReplayCause,
  ReplayClock,
  ReplayController,
  ReplayFrame,
  ReplayState,
  ReplayStatus,
  ReplayTimer,
} from "./replay/replayController";
export { createReplayController } from "./replay/replayController";

export type {
  ChartLayoutModel,
  ChartPanel,
  IndicatorRef,
  InstrumentRef,
  LayoutModelErrorCode,
  Watchlist,
  WatchlistEntry,
} from "./layout/layoutModel";
export {
  LAYOUT_MODEL_SCHEMA_VERSION,
  LayoutModelError,
  createEmptyLayoutModel,
  decodeLayoutModel,
  encodeLayoutModel,
} from "./layout/layoutModel";

export type {
  ChartingDrawingsRecord,
  ChartingLayoutRecord,
  ChartingPort,
  CreateLayoutInput,
  DeleteResult,
  DrawingsMeta,
  LayoutMeta,
  LoadResult,
  PutDrawingsInput,
  SaveResult,
  SavedDrawings,
  SavedLayout,
  UpdateLayoutInput,
} from "./layout/persistence";
export {
  createLayout,
  deleteLayout,
  loadDrawings,
  loadLayout,
  listLayouts,
  saveDrawings,
  saveLayout,
} from "./layout/persistence";

export type { AlignedPoint, AlignedSeries, AlignErrorCode } from "./compare/align";
export { AlignError, alignSeries } from "./compare/align";

export type { NormalizedPoint, NormalizeErrorCode } from "./compare/normalize";
export { NormalizeError, normalizeToBase100 } from "./compare/normalize";

export type { SpreadErrorCode, SpreadMode, SpreadPoint } from "./compare/spread";
export { SpreadError, spread } from "./compare/spread";

export type { AddPaneOptions, PaneKind, PaneModel, PaneModelErrorCode, PaneSpec } from "./panes/paneModel";
export {
  PaneModelError,
  addPane,
  createPaneModel,
  mainPane,
  removePane,
  resizePane,
  setHeightRatios,
} from "./panes/paneModel";

export type { PaneLayoutErrorCode, PaneRect, PaneScaleSet } from "./panes/paneLayout";
export { PaneLayoutError, computePaneRects, createPaneScaleSet } from "./panes/paneLayout";

export type { CrosshairMove, CrosshairSync, CrosshairSyncState } from "./panes/crosshairSync";
export { createCrosshairSync } from "./panes/crosshairSync";
