import { describe, expect, it } from "vitest";
import {
  DEMO_DATASET_DAYS,
  DEMO_INSTRUMENTS,
  findDemoInstrument,
  getDemoDailyCandles,
} from "./demoDataset";

describe("demoDataset", () => {
  it("샘플 종목 3개가 정의돼 있다(spec: 1년 M1 3종목)", () => {
    expect(DEMO_INSTRUMENTS).toHaveLength(3);
  });

  it("정의된 종목은 365개 일봉을 반환한다", () => {
    const bars = getDemoDailyCandles(DEMO_INSTRUMENTS[0].id);
    expect(bars).toHaveLength(DEMO_DATASET_DAYS);
  });

  it("같은 종목은 항상 같은 캔들을 반환한다(결정론적 고정 데이터셋 — Math.random 미사용)", () => {
    const first = getDemoDailyCandles(DEMO_INSTRUMENTS[1].id);
    const second = getDemoDailyCandles(DEMO_INSTRUMENTS[1].id);
    expect(second).toEqual(first);
  });

  it("서로 다른 종목은 서로 다른 캔들을 반환한다(테넌트/종목 간 데이터 격리)", () => {
    const a = getDemoDailyCandles(DEMO_INSTRUMENTS[0].id);
    const b = getDemoDailyCandles(DEMO_INSTRUMENTS[1].id);
    expect(a).not.toEqual(b);
  });

  it("negative: 알 수 없는 종목 id는 빈 배열을 반환한다", () => {
    expect(getDemoDailyCandles("NOT-A-DEMO-SYMBOL")).toEqual([]);
  });

  it("negative: findDemoInstrument는 알 수 없는 id에 undefined를 반환한다", () => {
    expect(findDemoInstrument("NOT-A-DEMO-SYMBOL")).toBeUndefined();
  });

  it("perf: 365일치 1분봉 집계(52.5만 스텝)가 200ms 이내에 끝난다", () => {
    // 이 파일의 앞선 테스트들이 이미 index 0/1을 캐시에 채워뒀으므로, 캐시
    // 미스 경로(실제 연산 비용)를 재려면 아직 쓰지 않은 index 2를 쓴다.
    const started = performance.now();
    getDemoDailyCandles(DEMO_INSTRUMENTS[2].id);
    const elapsedMs = performance.now() - started;
    expect(elapsedMs).toBeLessThan(200);
  });
});
