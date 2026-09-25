import { describe, expect, it } from "vitest";
import { createOverlayRegistry } from "../../indicators/overlayRegistry";
import { createPaneModel } from "../../panes/paneModel";
import { decodePlotSpec, PlotRenderError, type PlotSpec } from "../../render/plotRenderers";
import { createIndicatorPluginRegistry, IndicatorPluginError } from "../indicatorPlugin";
import {
  SCRIPT_PREVIEW_PLOT_SPEC,
  clearScriptPreview,
  syncScriptPreview,
  type ScriptCompilePreviewResult,
} from "../scriptPreview";
import { createRng } from "./arbitraries";

function compileResult(overrides: Partial<ScriptCompilePreviewResult> = {}): ScriptCompilePreviewResult {
  return { scriptHash: "a".repeat(64), resources: { plotCount: 1 }, ...overrides };
}

/** Runs `fn`, asserts it throws `IndicatorPluginError`, and returns its `.code`. */
function expectThrowsIndicatorPluginCode(fn: () => unknown): string {
  let caught: unknown;
  try {
    fn();
  } catch (err) {
    caught = err;
  }
  expect(caught).toBeInstanceOf(IndicatorPluginError);
  return (caught as IndicatorPluginError).code;
}

describe("syncScriptPreview", () => {
  it("registers one sub-pane per plotCount, keyed off the scriptHash", () => {
    const overlay = createOverlayRegistry();
    const result = syncScriptPreview(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      [],
      compileResult({ resources: { plotCount: 2 } }),
    );

    expect(result.instanceIds).toHaveLength(2);
    expect(result.registry.entries.map((e) => e.placement)).toEqual(["sub-pane", "sub-pane"]);
    // main pane + 2 registered sub-panes
    expect(result.paneModel.panes).toHaveLength(3);
    for (const id of result.instanceIds) {
      expect(result.plotSpecs.get(id)).toEqual(SCRIPT_PREVIEW_PLOT_SPEC);
    }
  });

  it("plotCount 0 tears down previous entries and registers none", () => {
    const overlay = createOverlayRegistry();
    const first = syncScriptPreview(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      [],
      compileResult({ resources: { plotCount: 2 } }),
    );

    const second = syncScriptPreview(
      first.registry,
      first.paneModel,
      overlay,
      first.instanceIds,
      compileResult({ resources: { plotCount: 0 } }),
    );

    expect(second.instanceIds).toEqual([]);
    expect(second.registry.entries).toEqual([]);
    expect(second.paneModel.panes).toHaveLength(1);
  });

  it("re-syncing with a new scriptHash replaces the old instances instead of accumulating", () => {
    const overlay = createOverlayRegistry();
    const first = syncScriptPreview(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      [],
      compileResult({ scriptHash: "a".repeat(64), resources: { plotCount: 1 } }),
    );

    const second = syncScriptPreview(
      first.registry,
      first.paneModel,
      overlay,
      first.instanceIds,
      compileResult({ scriptHash: "b".repeat(64), resources: { plotCount: 1 } }),
    );

    expect(second.instanceIds).toHaveLength(1);
    expect(second.instanceIds[0]).not.toEqual(first.instanceIds[0]);
    expect(second.registry.entries).toHaveLength(1);
    expect(second.paneModel.panes).toHaveLength(2);
  });

  it("recompiling with a smaller plotCount shrinks the sub-panes back down", () => {
    const overlay = createOverlayRegistry();
    const first = syncScriptPreview(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      [],
      compileResult({ resources: { plotCount: 3 } }),
    );

    const second = syncScriptPreview(
      first.registry,
      first.paneModel,
      overlay,
      first.instanceIds,
      compileResult({ resources: { plotCount: 1 } }),
    );

    expect(second.instanceIds).toHaveLength(1);
    expect(second.paneModel.panes).toHaveLength(2);
  });
});

describe("clearScriptPreview", () => {
  it("unregisters every previous instance and leaves only the main pane", () => {
    const overlay = createOverlayRegistry();
    const synced = syncScriptPreview(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      [],
      compileResult({ resources: { plotCount: 2 } }),
    );

    const cleared = clearScriptPreview(synced.registry, synced.paneModel, synced.instanceIds);

    expect(cleared.registry.entries).toEqual([]);
    expect(cleared.paneModel.panes).toHaveLength(1);
    expect(cleared.paneModel.panes[0]?.kind).toBe("main");
  });

  it("is a no-op when there is nothing to clear", () => {
    const paneModel = createPaneModel("main");
    const registry = createIndicatorPluginRegistry();
    const cleared = clearScriptPreview(registry, paneModel, []);

    expect(cleared.registry).toEqual(registry);
    expect(cleared.paneModel).toEqual(paneModel);
  });
});

describe("SCRIPT_PREVIEW_PLOT_SPEC", () => {
  it("is a valid IND-15 PlotSpec (decoded, not hand-typed)", () => {
    expect(SCRIPT_PREVIEW_PLOT_SPEC).toEqual({
      kind: "line",
      scale: "own",
      default_pane: "separate",
      fill_between: null,
      color_rule: null,
      precision: null,
      legend_format: null,
    });
  });
});

// DEPTH_CH(task-2729)가 원 task-1808(CH-12 scriptPreview.ts, 1ce7831)을 D2 축
// 하한 미달(D1)로 판정 -- 위 7 tests가 happy-path뿐이고 negative-path 3건
// 미만, 실패 주입, 수치 성능 단언, 게이트 적색 재현, D3가 전무했다. 이 leaf가
// 그 축들을 채운다. syncScriptPreview/clearScriptPreview 자체는 I/O 없는
// in-memory 함수라(CH-11 indicatorPlugin.ts DEEPEN, task-1731/ea522ceb와 동일
// 이유) 진짜 네트워크/DB 실패 주입은 구조적으로 불가능하다 -- 대신 caller가
// previousInstanceIds를 잘못 넘기는(낡은 참조, 재동기화 누락) malformed-input
// 도메인을 겨눈다.
describe("negative: throw/reject paths", () => {
  it("syncScriptPreview: previousInstanceIds가 registry에 없던 낡은 id를 담고 있으면 INDICATOR_PLUGIN_UNKNOWN을 던지고, 호출자의 registry/paneModel은 변형되지 않는다", () => {
    const overlay = createOverlayRegistry();
    const base = syncScriptPreview(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      [],
      compileResult({ resources: { plotCount: 2 } }),
    );
    const entriesBeforeLength = base.registry.entries.length;
    const panesBeforeLength = base.paneModel.panes.length;
    const staleIds = [...base.instanceIds, "ghost-instance"];

    const code = expectThrowsIndicatorPluginCode(() =>
      syncScriptPreview(base.registry, base.paneModel, overlay, staleIds, compileResult({ resources: { plotCount: 1 } })),
    );

    expect(code).toBe("INDICATOR_PLUGIN_UNKNOWN");
    // 낡은 id 이전에 나열된 유효한 id들이 먼저 해제됐더라도, 실패한 호출이
    // 그 중간 상태를 호출자에게 유출하지 않는다(각 함수는 언제나 새 객체만
    // 반환하고, 던지면 그 반환이 아예 일어나지 않는다).
    expect(base.registry.entries.length).toBe(entriesBeforeLength);
    expect(base.paneModel.panes.length).toBe(panesBeforeLength);
  });

  it("clearScriptPreview: registry에 한 번도 등록된 적 없는 id는 INDICATOR_PLUGIN_UNKNOWN으로 거부된다", () => {
    const overlay = createOverlayRegistry();
    const synced = syncScriptPreview(createIndicatorPluginRegistry(), createPaneModel("main"), overlay, [], compileResult());

    const code = expectThrowsIndicatorPluginCode(() => clearScriptPreview(synced.registry, synced.paneModel, ["never-registered"]));

    expect(code).toBe("INDICATOR_PLUGIN_UNKNOWN");
  });

  it("syncScriptPreview: 같은 scriptHash+plotCount가 이미 등록된 상태에서 previousInstanceIds를 []로(재동기화 누락) 넘기면 INDICATOR_PLUGIN_DUPLICATE를 던진다", () => {
    const overlay = createOverlayRegistry();
    const scriptHash = "c".repeat(64);
    const first = syncScriptPreview(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      [],
      compileResult({ scriptHash, resources: { plotCount: 1 } }),
    );

    const code = expectThrowsIndicatorPluginCode(() =>
      syncScriptPreview(first.registry, first.paneModel, overlay, [], compileResult({ scriptHash, resources: { plotCount: 1 } })),
    );

    expect(code).toBe("INDICATOR_PLUGIN_DUPLICATE");
  });

  it("clearScriptPreview: 유효한 id 다음에 낡은 id가 섞여 있으면 도중에 던지고, 호출자가 들고 있던 registry/paneModel 스냅샷은 그대로 남는다", () => {
    const overlay = createOverlayRegistry();
    const synced = syncScriptPreview(
      createIndicatorPluginRegistry(),
      createPaneModel("main"),
      overlay,
      [],
      compileResult({ resources: { plotCount: 2 } }),
    );
    const [validId] = synced.instanceIds;
    const entriesBeforeLength = synced.registry.entries.length;
    const panesBeforeLength = synced.paneModel.panes.length;

    const code = expectThrowsIndicatorPluginCode(() => clearScriptPreview(synced.registry, synced.paneModel, [validId!, "ghost"]));

    expect(code).toBe("INDICATOR_PLUGIN_UNKNOWN");
    expect(synced.registry.entries.length).toBe(entriesBeforeLength);
    expect(synced.paneModel.panes.length).toBe(panesBeforeLength);
  });
});

type PreviewFuzzCorruption = "extra_ghost_id" | "duplicate_id_in_list" | "resync_without_previous_ids";
const PREVIEW_FUZZ_CORRUPTIONS: readonly PreviewFuzzCorruption[] = [
  "extra_ghost_id",
  "duplicate_id_in_list",
  "resync_without_previous_ids",
];

describe("failure-injection: 시드 기반 malformed previousInstanceIds fuzzer", () => {
  it("200개 시드 각각 3종 오염을 주입해도 항상 IndicatorPluginError를 던지고, 호출자의 registry/paneModel은 절대 변형되지 않는다", () => {
    const seenCorruptions = new Set<PreviewFuzzCorruption>();

    for (let seed = 0; seed < 200; seed += 1) {
      const rng = createRng(seed);
      const overlay = createOverlayRegistry();
      const scriptHash = `${seed.toString(16).padStart(4, "0")}${"e".repeat(60)}`;
      const plotCount = rng.int(1, 4);
      const base = syncScriptPreview(
        createIndicatorPluginRegistry(),
        createPaneModel("main"),
        overlay,
        [],
        compileResult({ scriptHash, resources: { plotCount } }),
      );
      const entriesBeforeLength = base.registry.entries.length;
      const panesBeforeLength = base.paneModel.panes.length;
      const corruption = rng.pick(PREVIEW_FUZZ_CORRUPTIONS);
      seenCorruptions.add(corruption);

      const code = expectThrowsIndicatorPluginCode(() => {
        if (corruption === "extra_ghost_id") {
          clearScriptPreview(base.registry, base.paneModel, [...base.instanceIds, `ghost-${seed}`]);
        } else if (corruption === "duplicate_id_in_list") {
          clearScriptPreview(base.registry, base.paneModel, [...base.instanceIds, base.instanceIds[0]!]);
        } else {
          syncScriptPreview(base.registry, base.paneModel, overlay, [], compileResult({ scriptHash, resources: { plotCount } }));
        }
      });

      expect(["INDICATOR_PLUGIN_UNKNOWN", "INDICATOR_PLUGIN_DUPLICATE"]).toContain(code);
      expect(base.registry.entries.length).toBe(entriesBeforeLength);
      expect(base.paneModel.panes.length).toBe(panesBeforeLength);
    }

    expect(seenCorruptions.size).toBe(PREVIEW_FUZZ_CORRUPTIONS.length);
  });
});

describe("수치 성능: 대량 plotCount 동기 처리", () => {
  it("plotCount 500개를 등록한 뒤 20회 재컴파일 사이클(항상 500개 유지)까지 예산 안에 끝난다(동기 블로킹 회귀 감지)", () => {
    const overlay = createOverlayRegistry();
    let registry = createIndicatorPluginRegistry();
    let paneModel = createPaneModel("main");
    let instanceIds: readonly string[] = [];

    const startedAt = performance.now();
    for (let i = 0; i < 21; i += 1) {
      const scriptHash = `${i.toString(16).padStart(4, "0")}${"f".repeat(60)}`;
      const next = syncScriptPreview(registry, paneModel, overlay, instanceIds, compileResult({ scriptHash, resources: { plotCount: 500 } }));
      registry = next.registry;
      paneModel = next.paneModel;
      instanceIds = next.instanceIds;
    }
    const elapsedMs = performance.now() - startedAt;

    expect(instanceIds).toHaveLength(500);
    expect(registry.entries).toHaveLength(500);
    expect(paneModel.panes).toHaveLength(501);
    // 목적은 정밀 임계값이 아니라 회귀(예: O(n^2) teardown/rebuild)가 생기면
    // 이 값이 신호를 준다는 것 -- CI 환경 편차를 감안해 넉넉히 둔다.
    expect(elapsedMs).toBeLessThan(2000);
  });
});

// task-618 계열과 동일 기법: 모듈 docstring이 스스로 밝힌 이유("decodePlotSpec을
// 통해 디코드하므로 IND-15 필드 리네임이 조용히 드리프트하지 않는다")가 실제로
// 어떤 회귀를 적색으로 잡아내는지, 그 자리에 순진한 캐스트를 나란히 실행해
// 증명한다.
describe("게이트 적색 재현: decodePlotSpec 우회 시 IND-15 드리프트가 조용히 통과한다", () => {
  it("실제 경로(decodePlotSpec)는 리네임된 필드를 즉시 던지지만(green), 순진한 타입 캐스트는 조용히 깨진 스펙을 통과시킨다(red)", () => {
    // 백엔드 core/indicators/spec.py의 default_pane이 defaultPane으로
    // 리네임되는 드리프트를 시뮬레이션한다.
    const driftedRaw = {
      kind: "line",
      scale: "own",
      defaultPane: "separate",
      fill_between: null,
      color_rule: null,
      precision: null,
      legend_format: null,
    };

    // 실제 scriptPreview.ts가 쓰는 경로: 드리프트를 loud하게 잡는다.
    expect(() => decodePlotSpec(driftedRaw)).toThrow(PlotRenderError);
    expect(() => decodePlotSpec(driftedRaw)).toThrow(/unknown field "defaultPane"/);

    // 회귀: decodePlotSpec을 건너뛰고 컴파일 타임 캐스트만 믿는 naive 구현이었다면.
    const naiveSpec = driftedRaw as unknown as PlotSpec;
    // 필드가 없으니 런타임엔 조용히 undefined로 깨진다 -- 렌더 시점까지 아무도 모른다.
    expect(naiveSpec.default_pane).toBeUndefined();
    // 실제 검증기에 다시 통과시켜 보면 naive 캐스트 결과조차 유효한 PlotSpec이 아님이 드러난다.
    expect(() => decodePlotSpec(naiveSpec)).toThrow(PlotRenderError);
  });
});

describe("D3: 시드 프로퍼티 + 다중 인스턴스 격리", () => {
  it("무작위 sync 시퀀스마다 registry entries 수 == instanceIds 길이 == 서브페인 수 == plotSpecs 크기 불변식이 유지된다(200 시드)", () => {
    for (let seed = 0; seed < 200; seed += 1) {
      const rng = createRng(seed);
      const overlay = createOverlayRegistry();
      let registry = createIndicatorPluginRegistry();
      let paneModel = createPaneModel("main");
      let instanceIds: readonly string[] = [];

      const steps = rng.int(3, 8);
      for (let step = 0; step < steps; step += 1) {
        const plotCount = rng.int(0, 5);
        const scriptHash = `${seed}-${step}`.padEnd(64, "0");
        const synced = syncScriptPreview(registry, paneModel, overlay, instanceIds, compileResult({ scriptHash, resources: { plotCount } }));
        registry = synced.registry;
        paneModel = synced.paneModel;
        instanceIds = synced.instanceIds;

        expect(registry.entries).toHaveLength(plotCount);
        expect(instanceIds).toHaveLength(plotCount);
        expect(paneModel.panes).toHaveLength(plotCount + 1);
        expect(synced.plotSpecs.size).toBe(plotCount);
        for (const spec of synced.plotSpecs.values()) {
          expect(spec).toEqual(SCRIPT_PREVIEW_PLOT_SPEC);
        }
        expect(paneModel.panes.filter((p) => p.kind === "main")).toHaveLength(1);
      }
    }
  });

  it("두 독립 세션을 인터리브로 동기화해도 instanceId나 서브페인이 서로 앨리어싱되지 않는다", () => {
    const overlayA = createOverlayRegistry();
    const overlayB = createOverlayRegistry();
    let a = { registry: createIndicatorPluginRegistry(), paneModel: createPaneModel("main-a"), instanceIds: [] as readonly string[] };
    let b = { registry: createIndicatorPluginRegistry(), paneModel: createPaneModel("main-b"), instanceIds: [] as readonly string[] };

    for (let round = 0; round < 5; round += 1) {
      const syncedA = syncScriptPreview(
        a.registry,
        a.paneModel,
        overlayA,
        a.instanceIds,
        compileResult({ scriptHash: `a-${round}`.padEnd(64, "0"), resources: { plotCount: round + 1 } }),
      );
      a = { registry: syncedA.registry, paneModel: syncedA.paneModel, instanceIds: syncedA.instanceIds };

      const syncedB = syncScriptPreview(
        b.registry,
        b.paneModel,
        overlayB,
        b.instanceIds,
        compileResult({ scriptHash: `b-${round}`.padEnd(64, "0"), resources: { plotCount: round + 2 } }),
      );
      b = { registry: syncedB.registry, paneModel: syncedB.paneModel, instanceIds: syncedB.instanceIds };
    }

    expect(a.instanceIds).toHaveLength(5);
    expect(b.instanceIds).toHaveLength(6);
    expect(a.instanceIds.filter((id) => b.instanceIds.includes(id))).toEqual([]);
    expect(a.paneModel.panes).toHaveLength(6);
    expect(b.paneModel.panes).toHaveLength(7);
    expect(a.registry.entries).toHaveLength(5);
    expect(b.registry.entries).toHaveLength(6);
  });
});
