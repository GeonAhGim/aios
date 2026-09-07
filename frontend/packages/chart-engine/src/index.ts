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

export type {
  IndicatorCatalogEntry,
  IndicatorCatalogLoadResult,
  IndicatorCatalogPage,
  IndicatorCatalogPort,
  IndicatorCatalogQuery,
  IndicatorPluginDefinition,
  IndicatorPluginEntry,
  IndicatorPluginErrorCode,
  IndicatorPluginParams,
  IndicatorPluginRegistration,
  IndicatorPluginRegistry,
  IndicatorStyle,
  IndicatorStyleOutput,
  IndicatorTier,
} from "./plugins/indicatorPlugin";
export {
  IndicatorPluginError,
  createIndicatorPluginRegistry,
  decodeIndicatorStyle,
  encodeIndicatorStyle,
  loadIndicatorCatalog,
  registerIndicatorPlugin,
  setIndicatorPluginStyle,
  unregisterIndicatorPlugin,
} from "./plugins/indicatorPlugin";

export type { CrosshairValueOptions, StatusLineOptions } from "./legend/statusLine";
export { buildStatusLineLegends, createCrosshairValueStyle, createStatusLineTooltipStyle } from "./legend/statusLine";

export type {
  DataWindowErrorCode,
  DataWindowOptions,
  DataWindowRow,
  IndicatorFigureSource,
  IndicatorSeriesSnapshot,
} from "./legend/dataWindow";
export {
  DataWindowError,
  computeDataWindowRows,
  createIndicatorLastValueMarkStyle,
  createIndicatorTooltipStyle,
} from "./legend/dataWindow";

export type {
  IndicatorSource,
  ObjectKind,
  ObjectTreeEntry,
  ObjectTreeErrorCode,
  ObjectTreeSource,
  ObjectTreeState,
  ObjectTreeStateErrorCode,
  OverlaySource,
} from "./legend/objectTree";
export {
  ObjectTreeError,
  ObjectTreeStateError,
  applyObjectTreeState,
  buildObjectTree,
  encodeObjectTreeState,
  moveEntry,
  setEntryLocked,
  setEntryVisible,
} from "./legend/objectTree";

export type { Template, TemplateErrorCode, TemplateIndicator, TemplatePane } from "./templates/templateModel";
export { TEMPLATE_SCHEMA_VERSION, TemplateError, capture, decodeTemplate, encodeTemplate } from "./templates/templateModel";

export type {
  ApplyTemplateInput,
  ApplyTemplatePlan,
  ApplyTemplateResult,
  IndicatorRegistrationPlanEntry,
} from "./templates/applyTemplate";
export { apply } from "./templates/applyTemplate";

export type {
  DefaultPane,
  HistogramBar,
  Point2D,
  PlotKind,
  PlotRenderErrorCode,
  PlotRenderTarget,
  PlotSeriesPoint,
  PlotSpec,
  ScaleHint,
} from "./render/plotRenderers";
export { PlotRenderError, decodePlotSpec, deriveOverlayPlotSpec, renderPlot } from "./render/plotRenderers";

export type { FillBetweenErrorCode, FillDirection, FillSegment, FillSegmentPoint, FillSeriesPoint } from "./render/fillBetween";
export { FillBetweenError, computeFillSegments } from "./render/fillBetween";

export type { PlotProjection, ScaleBindingContext, ScaleBindingErrorCode } from "./render/scaleBinding";
export { ScaleBindingError, bindScale } from "./render/scaleBinding";

export type { LodCandle, LodErrorCode } from "./render/lod";
export { LodError, downsampleLOD } from "./render/lod";

export type { ViewportCullResult, ViewportErrorCode, ViewportRange } from "./render/viewport";
export { ViewportError, cullToViewport } from "./render/viewport";

export type { ScriptCompilePreviewResult, ScriptPreviewSyncResult } from "./plugins/scriptPreview";
export { SCRIPT_PREVIEW_PLOT_SPEC, clearScriptPreview, syncScriptPreview } from "./plugins/scriptPreview";

export type { VerifiedKernelPin } from "./compute/verifiedIndicators";
export { VERIFIED_KERNEL_PINS, isVerifiedIndicator, resolveVerifiedIndicators } from "./compute/verifiedIndicators";

export type {
  Bar,
  ClientEngineErrorCode,
  ComputeIndicatorSeriesArgs,
  IncrementalIndicator,
  IndicatorOutputs,
  IndicatorParams,
  IndicatorSeriesResult,
} from "./compute/clientEngine";
export {
  CLIENT_ENGINE_COMPUTE_TASK,
  ClientEngineError,
  KERNEL_FACTORIES,
  computeIndicatorSeries,
  createClientIncrementalIndicator,
} from "./compute/clientEngine";

export type {
  IndicatorWorkerMessage,
  IndicatorWorkerResponse,
  WorkerPool,
  WorkerPoolBackend,
  WorkerPoolErrorCode,
  WorkerPoolHandler,
} from "./compute/workerPool";
export {
  WorkerPoolError,
  createBrowserWorkerPoolBackend,
  createInlineWorkerPoolBackend,
  createWorkerPool,
} from "./compute/workerPool";
