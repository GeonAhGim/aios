// CH-6a 순수 이동(task-2011): ChartPage.tsx의 "레전드/오브젝트 트리 상태" 소유 단위를
// 그대로 옮긴다 — 로직 변경 없음. 후속 task-2013(objectTree 영속화)이 이 경계 위에 얹힌다.
import { type Dispatch, type SetStateAction, useMemo, useState } from "react";
import { createDefaultOverlayRegistry, type OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { TemplateApplyResult } from "./ChartTemplates";

export interface UseIndicatorSelectionResult {
  readonly selectedIndicatorIds: string[];
  readonly setSelectedIndicatorIds: Dispatch<SetStateAction<string[]>>;
  readonly overlayEntries: readonly OverlayEntry[];
  readonly knownIndicatorIds: Set<string>;
  readonly selectedOverlayEntries: OverlayEntry[];
  readonly mainOverlayEntries: OverlayEntry[];
  readonly subOverlayEntries: OverlayEntry[];
  readonly appliedPaneHeightRatios: Readonly<Record<string, number>> | undefined;
  readonly paneRemountKey: number;
  readonly toggleIndicator: (id: string) => void;
  readonly handleTemplateApplied: (result: TemplateApplyResult) => void;
}

export function useIndicatorSelection(): UseIndicatorSelectionResult {
  const [selectedIndicatorIds, setSelectedIndicatorIds] = useState<string[]>([]);
  const overlayEntries: readonly OverlayEntry[] = useMemo(() => createDefaultOverlayRegistry().list(), []);
  const knownIndicatorIds = useMemo(() => new Set(overlayEntries.map((e) => e.id)), [overlayEntries]);
  // CH-17c: 템플릿 적용 시 CH-14 페인 배치를 재현한다 — ChartPanes.tsx의
  // restoredHeightRatios는 최초 마운트 때만 적용되므로(파일 상단 주석), key를
  // 올려 다시 마운트시켜야 실제로 반영된다.
  const [appliedPaneHeightRatios, setAppliedPaneHeightRatios] = useState<Readonly<Record<string, number>> | undefined>(
    undefined,
  );
  const [paneRemountKey, setPaneRemountKey] = useState(0);
  // CH-14 화면 배선: 서브패널 존재 여부는 이미 CH-8로 저장되는 selectedIndicatorIds에서
  // 파생한다(ChartPanes.tsx 상단 주석 — 새 저장 경로를 만들지 않는다).
  const selectedOverlayEntries = useMemo(
    () => overlayEntries.filter((e) => selectedIndicatorIds.includes(e.id)),
    [overlayEntries, selectedIndicatorIds],
  );
  const mainOverlayEntries = useMemo(
    () => selectedOverlayEntries.filter((e) => e.placement === "main-overlay"),
    [selectedOverlayEntries],
  );
  const subOverlayEntries = useMemo(
    () => selectedOverlayEntries.filter((e) => e.placement === "sub-pane"),
    [selectedOverlayEntries],
  );

  function toggleIndicator(id: string): void {
    setSelectedIndicatorIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  function handleTemplateApplied(result: TemplateApplyResult): void {
    setSelectedIndicatorIds([...result.indicatorIds]);
    setAppliedPaneHeightRatios({ ...result.paneHeightRatios });
    setPaneRemountKey((prev) => prev + 1);
  }

  return {
    selectedIndicatorIds,
    setSelectedIndicatorIds,
    overlayEntries,
    knownIndicatorIds,
    selectedOverlayEntries,
    mainOverlayEntries,
    subOverlayEntries,
    appliedPaneHeightRatios,
    paneRemountKey,
    toggleIndicator,
    handleTemplateApplied,
  };
}
