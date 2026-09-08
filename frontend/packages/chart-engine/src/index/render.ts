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
} from "../render/plotRenderers";
export { PlotRenderError, decodePlotSpec, deriveOverlayPlotSpec, renderPlot } from "../render/plotRenderers";

export type { FillBetweenErrorCode, FillDirection, FillSegment, FillSegmentPoint, FillSeriesPoint } from "../render/fillBetween";
export { FillBetweenError, computeFillSegments } from "../render/fillBetween";

export type { PlotProjection, ScaleBindingContext, ScaleBindingErrorCode } from "../render/scaleBinding";
export { ScaleBindingError, bindScale } from "../render/scaleBinding";

export type { LodCandle, LodErrorCode } from "../render/lod";
export { LodError, downsampleLOD } from "../render/lod";

export type { ViewportCullResult, ViewportErrorCode, ViewportRange } from "../render/viewport";
export { ViewportError, cullToViewport } from "../render/viewport";
