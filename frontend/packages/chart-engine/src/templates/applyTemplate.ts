/**
 * CH-17a — apply an indicator template to a chart panel.
 *
 * `apply()` computes what the panel's pane layout and indicator list should
 * become, plus a plan of which indicator ids need registering/unregistering
 * to match — it never registers/unregisters anything itself. Actual
 * indicator (de)registration belongs to CH-11's registry (a separate,
 * in-progress leaf); this module only ever returns a plan for that caller to
 * act on.
 *
 * Pane manipulation reuses CH-14 `panes/paneModel.ts` (`createPaneModel`,
 * `addPane`, `setHeightRatios`) instead of re-deriving the height-ratio
 * invariant here.
 */

import { addPane, createPaneModel, setHeightRatios } from "../panes/paneModel";
import type { PaneModel } from "../panes/paneModel";
import type { ChartLayoutModel, IndicatorRef } from "../layout/layoutModel";
import { TemplateError } from "./templateModel";
import type { Template } from "./templateModel";

export interface ApplyTemplateInput {
  readonly paneModel: PaneModel;
  readonly layoutModel: ChartLayoutModel;
  /** Indicator ids the caller's registry currently knows how to render. */
  readonly knownIndicatorIds: ReadonlySet<string>;
}

export interface IndicatorRegistrationPlanEntry {
  readonly indicatorId: string;
  readonly paneId: string;
  readonly params?: Readonly<Record<string, number>>;
}

export interface ApplyTemplatePlan {
  /** Indicators the template calls for that the current panel doesn't have yet. */
  readonly toRegister: readonly IndicatorRegistrationPlanEntry[];
  /** Indicator ids the current panel has that the template doesn't call for. */
  readonly toUnregister: readonly string[];
}

export interface ApplyTemplateResult {
  readonly paneModel: PaneModel;
  readonly layoutModel: ChartLayoutModel;
  readonly plan: ApplyTemplatePlan;
}

function buildPaneModel(template: Template): PaneModel {
  const mainPane = template.panes.find((pane) => pane.kind === "main");
  if (!mainPane) throw new TemplateError("field_invalid", "panes: template has no main pane");

  let model = createPaneModel(mainPane.id);
  for (const pane of template.panes) {
    if (pane.id === mainPane.id) continue;
    model = addPane(model, pane.id);
  }
  // A lone main pane always has heightRatio 1 by construction — setHeightRatios
  // rejects exactly 1 (it requires the open interval (0, 1)), so skip it here.
  if (template.panes.length === 1) return model;

  const ratios: Record<string, number> = {};
  for (const pane of template.panes) ratios[pane.id] = pane.heightRatio;
  return setHeightRatios(model, ratios);
}

/**
 * Computes the plan and resulting model shapes for applying `template`.
 * Fail-closed: any indicator id not in `knownIndicatorIds`, or a missing
 * active panel, is rejected before anything is built — `input.paneModel`
 * and `input.layoutModel` are never mutated, on the error path or otherwise.
 */
export function apply(template: Template, input: ApplyTemplateInput): ApplyTemplateResult {
  for (const indicator of template.indicators) {
    if (!input.knownIndicatorIds.has(indicator.id)) {
      throw new TemplateError("unknown_indicator", `indicator "${indicator.id}" is not in knownIndicatorIds`);
    }
  }
  if (input.layoutModel.activePanelId === null) {
    throw new TemplateError("no_active_panel", "layoutModel.activePanelId is null");
  }
  const panelIndex = input.layoutModel.panels.findIndex((p) => p.id === input.layoutModel.activePanelId);
  if (panelIndex < 0) {
    throw new TemplateError("panel_not_found", `no panel with id "${input.layoutModel.activePanelId}"`);
  }
  const currentPanel = input.layoutModel.panels[panelIndex]!;

  const nextPaneModel = buildPaneModel(template);

  const nextIndicators: IndicatorRef[] = template.indicators.map((indicator) =>
    indicator.params === undefined ? { id: indicator.id } : { id: indicator.id, params: indicator.params },
  );
  const nextPanels = input.layoutModel.panels.map((panel, index) =>
    index === panelIndex ? { ...panel, indicators: nextIndicators } : panel,
  );
  const nextLayoutModel: ChartLayoutModel = { ...input.layoutModel, panels: nextPanels };

  const currentIds = new Set(currentPanel.indicators.map((indicator) => indicator.id));
  const templateIds = new Set(template.indicators.map((indicator) => indicator.id));
  const toRegister: IndicatorRegistrationPlanEntry[] = template.indicators
    .filter((indicator) => !currentIds.has(indicator.id))
    .map((indicator) =>
      indicator.params === undefined
        ? { indicatorId: indicator.id, paneId: indicator.paneId }
        : { indicatorId: indicator.id, paneId: indicator.paneId, params: indicator.params },
    );
  const toUnregister: string[] = currentPanel.indicators
    .filter((indicator) => !templateIds.has(indicator.id))
    .map((indicator) => indicator.id);

  return { paneModel: nextPaneModel, layoutModel: nextLayoutModel, plan: { toRegister, toUnregister } };
}
