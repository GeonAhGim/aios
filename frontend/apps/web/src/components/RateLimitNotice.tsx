import { useEffect, useState } from "react";
import { RATE_LIMIT_ERROR_CODE, getApiErrorMessage } from "@aios/shared-types";
import { Alert, Button } from "@aios/ui-web";
import { useRateLimitNotice } from "../hooks/useRetryableAction";

// task-841(§3.3 error taxonomy, §9 PLT-25): useRetryableAction을 쓰는 어느 화면이든
// 429 RATE_LIMIT_EXCEEDED로 백오프 대기 중이면 이 배너 하나가 화면 상단에 뜬다.
// MfaStepUpDialog(task-481)와 같은 "앱 루트 1곳 마운트 + 전역 store 구독" 배선이다.
// 실제 재시도 스케줄링·재시도 자체는 useRetryableAction.run()이 이미 하므로
// 여기서는 표시(카운트다운)와 조기 재시도 트리거(retryNow)만 담당한다.
//
// 카운트다운은 이 컴포넌트만의 로컬 타이머다(ErrorMessage와 동일한 방식) — run()의
// 실제 대기가 끝나면 notice가 null이 되어 배너가 사라지므로, 이 타이머는 화면
// 표시용이고 실제 재시도 타이밍을 결정하지 않는다.
//
// "다시 시도" 버튼은 카운트다운이 끝나야 눌리는 ErrorMessage 방식과 다르게 항상
// 눌러진다 — 비활성 탭에서는 브라우저가 실제 대기 타이머를 스로틀링해 카운트다운과
// 실제 재시도 시점이 어긋날 수 있으므로, 버튼은 "표시된 대기가 끝나길 기다리는"
// 확인 버튼이 아니라 그 대기 자체를 건너뛰는 수단이다(retryNow → interruptibleSleep.skip).
export function RateLimitNotice() {
  const notice = useRateLimitNotice();
  // notice는 useSyncExternalStore 알림(렌더 바깥 이벤트)으로 바뀌므로, 새 notice의
  // retryAfterSec 반영을 useEffect에 맡기면 "이전 remainingSec으로 한 번 더 렌더된
  // 프레임"이 실제로 보인다(예: 5초짜리 새 notice인데 잠깐 0초 문구가 뜸). 렌더링
  // 도중에 상태를 맞추는 React 공식 패턴(state derived from changed prop)으로
  // 그 프레임 자체를 없앤다.
  const [trackedNotice, setTrackedNotice] = useState(notice);
  const [remainingSec, setRemainingSec] = useState(notice?.retryAfterSec ?? 0);
  if (notice !== trackedNotice) {
    setTrackedNotice(notice);
    setRemainingSec(notice?.retryAfterSec ?? 0);
  }

  useEffect(() => {
    if (!notice || remainingSec <= 0) return;
    const timer = setTimeout(() => setRemainingSec((prev) => Math.max(0, prev - 1)), 1000);
    return () => clearTimeout(timer);
  }, [notice, remainingSec]);

  if (!notice) return null;

  return (
    <div className="fixed inset-x-0 top-0 z-40 flex justify-center px-4 py-2">
      <div className="w-full max-w-xl">
        <Alert tone="warning">
          <div className="flex items-center justify-between gap-3">
            <p>{getApiErrorMessage(RATE_LIMIT_ERROR_CODE)}</p>
            <div className="flex shrink-0 items-center gap-2">
              <span className="text-xs text-fg-muted">
                {remainingSec > 0 ? `${remainingSec}초 후 자동으로 다시 시도합니다.` : "곧 다시 시도합니다."}
              </span>
              <Button size="sm" variant="secondary" onClick={notice.retryNow}>
                지금 다시 시도
              </Button>
            </div>
          </div>
        </Alert>
      </div>
    </div>
  );
}
