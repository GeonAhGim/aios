// CH-17c — 화면 조립: "현재 차트 → 템플릿 저장" · "템플릿 선택 → 적용"을 CH-17a
// (chart-engine templates/{templateModel,applyTemplate}.ts, task-1809 ff52230)
// capture()/apply()에 그대로 위임한다(decision: 적용 로직을 새로 짜지 않는다).
//
// ChartPanes.tsx가 이미 쓰는 CH-14 paneModel 관용(메인 페인 "main" + 서브패널
// "sub-<indicatorId>")을 여기서 되짚어 만든다 — ChartPanes 내부의 실제 paneModel/
// objectTree 상태는 그 컴포넌트 밖으로 노출된 적이 없다(hiddenIds·드래그 리사이즈는
// 여전히 서버에 영속화되지 않는다는 CH-14 리프의 결정 그대로) — 그래서 캡처되는
// 페인 높이는 항상 균등분할 기본값이다. 비교 심볼(CH-13b)은 템플릿 범위 밖이다:
// capture/apply는 오버레이 레지스트리 지표 id만 다룬다.
//
// 목록 조회(GET /indicator-templates)는 IndicatorPicker.tsx의 열림-지연 관용과
// 동일하게 팝오버가 열렸을 때만(enabled: open) 나간다 — 화면 마운트 시 항상
// 나가지 않는다.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, type ChartIndicatorTemplateRecord, type CreateChartIndicatorTemplateInput } from "@aios/api-client";
import { apply } from "@aios/chart-engine/src/templates/applyTemplate";
import { capture, decodeTemplate, encodeTemplate, type Template } from "@aios/chart-engine/src/templates/templateModel";
import type { ChartLayoutModel, ChartPanel } from "@aios/chart-engine/src/layout/layoutModel";
import { addPane, createPaneModel, type PaneModel } from "@aios/chart-engine/src/panes/paneModel";
import type { ObjectTreeEntry } from "@aios/chart-engine/src/legend/objectTree";
import { routeApiError } from "@aios/shared-types";
import { Button, EmptyState, Input, LoadingState } from "@aios/ui-web";
import { ErrorMessage } from "../../components/ErrorMessage";

const TEMPLATE_PANEL_ID = "template-panel";
const MAIN_PANE_ID = "main";
const TEMPLATES_QUERY_KEY = ["chart-indicator-templates"];

// ChartPanes.tsx의 subPaneId()와 반드시 같은 형식이어야 한다 — 적용 결과의
// paneHeightRatios가 그 화면의 실제 paneModel id와 맞아야 restoredHeightRatios가
// PANE_NOT_FOUND 없이 먹는다.
function subPaneId(indicatorId: string): string {
  return `sub-${indicatorId}`;
}

export interface TemplateApplyResult {
  readonly indicatorIds: readonly string[];
  readonly paneHeightRatios: Readonly<Record<string, number>>;
}

export interface ChartTemplatesPort {
  createIndicatorTemplate(input: CreateChartIndicatorTemplateInput): Promise<ChartIndicatorTemplateRecord>;
  listIndicatorTemplates(): Promise<readonly ChartIndicatorTemplateRecord[]>;
  deleteIndicatorTemplate(templateId: string): Promise<void>;
}

export interface ChartTemplatesProps {
  readonly port: ChartTemplatesPort;
  /** 현재 화면의 메인 오버레이 배치 지표 id(순서 유지). */
  readonly mainIndicatorIds: readonly string[];
  /** 현재 화면의 서브패널 배치 지표 id(순서 유지). */
  readonly subIndicatorIds: readonly string[];
  /** 오버레이 레지스트리가 실제로 그릴 수 있는 지표 id 전체(CH-17a apply()의 fail-closed 검사용). */
  readonly knownIndicatorIds: ReadonlySet<string>;
  readonly onApplied: (result: TemplateApplyResult) => void;
}

function buildPaneModel(subIds: readonly string[]): PaneModel {
  return subIds.reduce((model, id) => addPane(model, subPaneId(id)), createPaneModel(MAIN_PANE_ID));
}

function buildInventory(mainIds: readonly string[], subIds: readonly string[]): readonly ObjectTreeEntry[] {
  return [
    ...mainIds.map((id) => ({ id, kind: "indicator" as const, paneId: MAIN_PANE_ID, name: id, visible: true, locked: false })),
    ...subIds.map((id) => ({ id, kind: "indicator" as const, paneId: subPaneId(id), name: id, visible: true, locked: false })),
  ];
}

function buildLayoutModel(indicatorIds: readonly string[]): ChartLayoutModel {
  const panel: ChartPanel = {
    id: TEMPLATE_PANEL_ID,
    instrument: { instrumentId: "template", venue: "TEMPLATE", symbol: "template" },
    timeframe: "1h",
    indicators: indicatorIds.map((id) => ({ id })),
    drawingSetId: TEMPLATE_PANEL_ID,
  };
  return { schemaVersion: 1, panels: [panel], activePanelId: TEMPLATE_PANEL_ID, watchlists: [] };
}

function captureCurrent(mainIds: readonly string[], subIds: readonly string[]): Template {
  return capture(buildInventory(mainIds, subIds), buildPaneModel(subIds), buildLayoutModel([...mainIds, ...subIds]));
}

function applyDecoded(
  template: Template,
  currentIndicatorIds: readonly string[],
  knownIndicatorIds: ReadonlySet<string>,
): TemplateApplyResult {
  const result = apply(template, {
    paneModel: createPaneModel(MAIN_PANE_ID),
    layoutModel: buildLayoutModel(currentIndicatorIds),
    knownIndicatorIds,
  });
  const panel = result.layoutModel.panels.find((p) => p.id === TEMPLATE_PANEL_ID);
  const indicatorIds = panel ? panel.indicators.map((i) => i.id) : [];
  const paneHeightRatios: Record<string, number> = {};
  for (const pane of result.paneModel.panes) paneHeightRatios[pane.id] = pane.heightRatio;
  return { indicatorIds, paneHeightRatios };
}

function TemplateOpError({ error }: { error: unknown }) {
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
    />
  );
}

export function ChartTemplates({ port, mainIndicatorIds, subIndicatorIds, knownIndicatorIds, onApplied }: ChartTemplatesProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [captureError, setCaptureError] = useState<unknown>(null);
  const [applyError, setApplyError] = useState<unknown>(null);
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: TEMPLATES_QUERY_KEY,
    queryFn: () => port.listIndicatorTemplates(),
    enabled: open,
  });

  const saveMutation = useMutation({
    mutationFn: (input: CreateChartIndicatorTemplateInput) => port.createIndicatorTemplate(input),
    onSuccess: () => {
      setName("");
      void queryClient.invalidateQueries({ queryKey: TEMPLATES_QUERY_KEY });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (templateId: string) => port.deleteIndicatorTemplate(templateId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: TEMPLATES_QUERY_KEY }),
  });

  function handleSave(): void {
    setCaptureError(null);
    const trimmed = name.trim();
    if (trimmed.length === 0) return;
    try {
      const template = captureCurrent(mainIndicatorIds, subIndicatorIds);
      saveMutation.mutate({ name: trimmed, template: encodeTemplate(template) });
    } catch (err) {
      setCaptureError(err);
    }
  }

  function handleApply(record: ChartIndicatorTemplateRecord): void {
    setApplyError(null);
    try {
      const template = decodeTemplate(record.template);
      onApplied(applyDecoded(template, [...mainIndicatorIds, ...subIndicatorIds], knownIndicatorIds));
    } catch (err) {
      setApplyError(err);
    }
  }

  const templates = query.data ?? [];
  const saveDisabled = name.trim().length === 0 || saveMutation.isPending;

  return (
    <div className="relative inline-block" data-testid="chart-templates">
      <Button type="button" variant="secondary" size="sm" aria-expanded={open} onClick={() => setOpen((prev) => !prev)}>
        템플릿
      </Button>

      {open && (
        <div className="absolute z-10 mt-1 w-80 space-y-3 rounded-md border border-border bg-surface p-3 shadow-lg">
          <div className="flex items-end gap-1.5">
            <Input
              aria-label="템플릿 이름"
              placeholder="템플릿 이름"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="flex-1"
            />
            <Button
              type="button"
              size="sm"
              disabled={saveDisabled}
              loading={saveMutation.isPending}
              onClick={handleSave}
              data-testid="chart-templates-save"
            >
              현재 차트 저장
            </Button>
          </div>
          {!!captureError && <TemplateOpError error={captureError} />}
          {saveMutation.isError && <TemplateOpError error={saveMutation.error} />}
          {!!applyError && <TemplateOpError error={applyError} />}

          {query.isError ? (
            <TemplateOpError error={query.error} />
          ) : query.isLoading ? (
            <LoadingState />
          ) : templates.length === 0 ? (
            <EmptyState>저장된 템플릿이 없습니다.</EmptyState>
          ) : (
            <ul aria-label="템플릿 목록" className="space-y-1">
              {templates.map((t) => (
                <li key={t.id} className="flex items-center justify-between gap-2 text-sm text-fg">
                  <span>{t.name}</span>
                  <span className="flex gap-1.5">
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => handleApply(t)}
                      data-testid={`chart-templates-apply-${t.id}`}
                    >
                      적용
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={deleteMutation.isPending}
                      onClick={() => deleteMutation.mutate(t.id)}
                      data-testid={`chart-templates-delete-${t.id}`}
                    >
                      삭제
                    </Button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
