import { useQuery } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

// spec §3.2/§9 PLT-09: `/readyz`는 1회성 진단 조회 — 자동 재시도·백그라운드
// 재검증을 하지 않는다(getReadiness가 이미 raw 몸체를 그대로 돌려주므로
// 저하 응답(503)도 catch가 아니라 성공 데이터로 들어올 수 있다 — 판정은
// 소비자가 parseReadiness로 한다).
export function usePlatformReadiness() {
  return useQuery({
    queryKey: ["platformReadiness"],
    queryFn: () => apiClient.getReadiness(),
    retry: false,
    refetchOnWindowFocus: false,
  });
}
