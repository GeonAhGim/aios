// spec §3.2 ReadinessReport 소비. api-client/readiness.ts(parseReadiness·summarizeReadiness)가
// 파싱·요약을 맡고, 이 훅은 useFieldErrors.ts와 같은 방식으로 그 위에 React 상태만
// 얹는다 — 훅 스스로 fetch/폴링하지 않고, 호출자가 받아온 raw 응답을 setFromResponse로
// 넣어주면 배너 표시 여부와 실패 원인 문자열만 돌려준다.
//
// 범위 제한(task-466 decision): 폴링 주기·재시도·전역 상태 도입 금지. 실제 fetch 배선은
// 이 리프의 범위 밖이다.
import { useCallback, useState } from "react";
import { parseReadiness, summarizeReadiness, type ReadinessSummary } from "@aios/api-client";

const UNKNOWN_SUMMARY: ReadinessSummary = { status: "unknown", failedChecks: [] };

export interface UseReadinessResult {
  /** status가 not_ready일 때만 true. unknown(응답 없음/파싱 실패)이나 ready는 false다. */
  showDegradedBanner: boolean;
  /** 실패한 check를 "이름: detail"(detail 없으면 이름만) 문자열로 나열한 목록. */
  failureReasons: string[];
  /** raw 응답(봉투 유무 무관)을 넣어 배너 상태를 갱신한다. */
  setFromResponse: (raw: unknown) => void;
}

function toFailureReason(name: string, detail: string | null): string {
  return detail ? `${name}: ${detail}` : name;
}

export function useReadiness(): UseReadinessResult {
  const [summary, setSummary] = useState<ReadinessSummary>(UNKNOWN_SUMMARY);

  const setFromResponse = useCallback((raw: unknown) => {
    setSummary(summarizeReadiness(parseReadiness(raw)));
  }, []);

  return {
    showDegradedBanner: summary.status === "not_ready",
    failureReasons: summary.failedChecks.map((fc) => toFailureReason(fc.name, fc.detail)),
    setFromResponse,
  };
}
