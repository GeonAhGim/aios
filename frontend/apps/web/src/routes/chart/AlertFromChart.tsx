import { useCreateAlert } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import {
  classifyBadRequest,
  classifyForbidden,
  routeApiError,
  type AlertCreateRequest,
  type Timeframe,
  type Venue,
} from "@aios/shared-types";
import { Alert, Button, Field, Input, Select } from "@aios/ui-web";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// CH-9 — AlertsPage(task-930/§3.3)와 동일한 에러 표시 조합을 그대로 재사용한다.
// 알림 생성 실패 판정·표시 로직을 이 파일에서 다시 구현하지 않는다(routeApiError
// 단일 진입점 → BadRequestNotice/ForbiddenNotice/ErrorMessage로만 노출).
function CreateAlertError({ error }: { error: unknown }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
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

// AlertCreateRequest.indicator는 백엔드 SUPPORTED_INDICATORS 화이트리스트만 받는다
// (src/core/indicators/talib_adapter.py) — "가격 그 자체"를 뜻하는 지표는 없다.
// TA-Lib SMA(timeperiod=1)은 입력 종가를 그대로 돌려주므로(가공 없음) SMA·period=1을
// 종가 자체로 취급해 서버 계약을 바꾸지 않고 기존 필드만으로 "가격" 조건을 표현한다.
const PRICE_INDICATOR = "SMA";
const PRICE_PARAMS = { timeperiod: 1 };

const VENUE_TO_EXCHANGE: Record<Venue, string> = {
  BITGET: "bitget",
  KIS_KRX: "kis",
  KIS_US: "kis",
};

type PriceDirection = "above" | "below";

const PRICE_OPERATOR: Record<PriceDirection, AlertCreateRequest["operator"]> = {
  above: ">",
  below: "<",
};

export interface AlertFromChartProps {
  isOpen: boolean;
  onClose: () => void;
  venue: Venue;
  instrumentId: string;
  timeframe: Timeframe;
  currentClose: number | null;
  /** IndicatorPicker(CH-6a)에서 켠 지표 id 목록 — 지표 조건이 비활성인 사유 문구에만 쓴다. */
  selectedIndicatorIds: readonly string[];
}

const TITLE_ID = "alert-from-chart-title";
const INDICATOR_REASON_ID = "alert-from-chart-indicator-reason";

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(
      'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
    ),
  );
}

export function AlertFromChart({
  isOpen,
  onClose,
  venue,
  instrumentId,
  timeframe,
  currentClose,
  selectedIndicatorIds,
}: AlertFromChartProps) {
  const createAlert = useCreateAlert();
  const dialogRef = useRef<HTMLDivElement>(null);
  const [direction, setDirection] = useState<PriceDirection>("above");
  const [threshold, setThreshold] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [createdId, setCreatedId] = useState<number | null>(null);

  // 열릴 때마다 현재 캔들 종가로 다시 프리필하고 이전 열림의 잔여 상태를 지운다.
  useEffect(() => {
    if (!isOpen) return;
    setDirection("above");
    setThreshold(currentClose !== null ? String(currentClose) : "");
    setError(null);
    setCreatedId(null);
    // currentClose는 열려 있는 동안 바뀌어도(새 봉 도착 등) 다시 덮어쓰지 않는다 —
    // 사용자가 이미 입력을 시작했을 수 있어 isOpen 전이(열릴 때 1회)에만 반응한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  // 포커스 트랩 + Esc 닫기 + 닫힌 뒤 트리거로 포커스 복귀.
  useEffect(() => {
    if (!isOpen) return undefined;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    focusableElements(dialog ?? document.body)[0]?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialog) return;
      const items = focusableElements(dialog);
      if (items.length === 0) return;
      const first = items[0]!;
      const last = items[items.length - 1]!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused?.focus();
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const created = await createAlert.mutateAsync({
        exchange: VENUE_TO_EXCHANGE[venue],
        symbol: instrumentId,
        timeframe,
        indicator: PRICE_INDICATOR,
        params: PRICE_PARAMS,
        operator: PRICE_OPERATOR[direction],
        threshold: Number(threshold),
      });
      setCreatedId(created.id);
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error("알림 생성에 실패했습니다."));
    }
  }

  const indicatorDisabledReason =
    selectedIndicatorIds.length > 0
      ? `선택한 지표(${selectedIndicatorIds.join(", ")})의 실시간 계산값이 차트에 아직 연동되지 않아 교차 알림은 준비 중입니다.`
      : "차트에 지표 값 계산이 아직 연동되지 않아 교차 알림은 준비 중입니다.";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={TITLE_ID}
        className="w-full max-w-md space-y-4 rounded-xl border border-border bg-surface p-6"
      >
        <h2 id={TITLE_ID} className="text-lg font-semibold text-fg">
          차트에서 알림 만들기
        </h2>
        <p className="text-sm text-fg-secondary">
          {instrumentId} · {timeframe} · 현재가 {currentClose ?? "—"}
        </p>

        {createdId !== null ? (
          <div className="space-y-3">
            <Alert tone="success">
              <p>알림이 등록되었습니다 (#{createdId}).</p>
            </Alert>
            <div className="flex justify-end gap-2">
              <Link to="/alerts" className="text-sm text-accent underline" onClick={onClose}>
                알림 목록에서 확인
              </Link>
              <Button type="button" variant="secondary" onClick={onClose}>
                닫기
              </Button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-3">
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium text-fg-secondary">조건</legend>
              <label htmlFor="alert-condition-price" className="flex items-center gap-2 text-sm text-fg">
                <input
                  id="alert-condition-price"
                  type="radio"
                  name="alert-condition-kind"
                  value="price"
                  checked
                  onChange={() => {}}
                />
                가격 above/below
              </label>
              <label
                htmlFor="alert-condition-indicator"
                className="flex items-center gap-2 text-sm text-fg-muted"
              >
                <input
                  id="alert-condition-indicator"
                  type="radio"
                  name="alert-condition-kind"
                  value="indicator"
                  disabled
                  checked={false}
                  onChange={() => {}}
                  aria-describedby={INDICATOR_REASON_ID}
                />
                지표 교차 (crosses above/below)
              </label>
              <p id={INDICATOR_REASON_ID} className="text-xs text-fg-muted">
                {indicatorDisabledReason}
              </p>
            </fieldset>

            <div className="grid grid-cols-2 gap-3">
              <Field label="방향" htmlFor="alert-direction">
                <Select
                  id="alert-direction"
                  value={direction}
                  onChange={(e) => setDirection(e.target.value as PriceDirection)}
                >
                  <option value="above">above (초과 시)</option>
                  <option value="below">below (미만 시)</option>
                </Select>
              </Field>
              <Field label="임계값" htmlFor="alert-threshold">
                <Input
                  id="alert-threshold"
                  type="number"
                  required
                  value={threshold}
                  onChange={(e) => setThreshold(e.target.value)}
                />
              </Field>
            </div>

            {error !== null && <CreateAlertError error={error} />}

            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={onClose}>
                취소
              </Button>
              <Button type="submit" loading={createAlert.isPending}>
                알림 등록
              </Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
