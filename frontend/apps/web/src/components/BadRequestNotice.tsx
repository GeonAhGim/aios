import { classifyBadRequest, extractFieldErrors, getApiErrorMessage } from "@aios/shared-types";
import { Alert, Button } from "@aios/ui-web";

// 400 응답을 classifyBadRequest(shared-types/badRequest.ts)의 다섯 갈래로 나눠 보여주는
// 표시 전용 컴포넌트. status가 400이 아니면(또는 error가 비어있으면) null을 반환해
// 기존 ErrorMessage 배너가 계속 담당하게 둔다.
//
// field 갈래는 원칙적으로 useFieldErrors가 폼 인라인으로 보여주므로(task-364) 이
// 배너는 렌더링하지 않는다(중복 안내 금지) — extractFieldErrors(shared-types,
// task-364 기존 export 재사용)로 뽑은 필드맵이 한 건이라도 있으면 null이다.
// 다만 details.fields가 비어있는 VALIDATION_INVALID_FIELD(task-1214: PLT-18/19
// 이후 PurchaseError·ExecutionCreateError·RebalanceError가 이 모양으로 옴)는 인라인이
// 뜰 자리가 없어 그대로 두면 화면이 통째로 무응답이 된다 — 이 경우에만 배너를 띄운다.
// 문구는 서버 message가 있으면 그것을, 없으면 apiError.ts의 EXACT_MESSAGES(taxonomy와
// 1:1) 문구를 쓰며, 그 선택은 이 컴포넌트 안 한 지점(fallbackText)에서만 결정한다 —
// err.message를 화면에 직접 노출하는 게 아니라 getApiErrorMessage의 fallbackMessage
// 인자로만 흘려보낸다(task-1048 가드가 승인하는 경로). disclosure_retired만 최신
// revision 재조회 버튼을, mfa_invalid만 코드 재입력 필드로 포커스를 옮기는 콜백
// 버튼을 보여준다 — 나머지 갈래는 재시도해도 같은 결과가 반복되므로 액션 버튼을
// 두지 않는다.
interface BadRequestNoticeProps {
  error?: unknown;
  onReload?: () => void;
  onFocusMfaCode?: () => void;
}

function extractErrorCode(error: unknown): string | undefined {
  if (typeof error !== "object" || error === null) return undefined;
  const errorCode = (error as { errorCode?: unknown }).errorCode;
  return typeof errorCode === "string" ? errorCode : undefined;
}

function extractMessage(error: unknown): string | undefined {
  if (typeof error !== "object" || error === null) return undefined;
  const message = (error as { message?: unknown }).message;
  return typeof message === "string" ? message : undefined;
}

export function BadRequestNotice({ error, onReload, onFocusMfaCode }: BadRequestNoticeProps) {
  const kind = classifyBadRequest(error);
  if (!kind) return null;

  const errorCode = extractErrorCode(error);
  const message = extractMessage(error);

  if (kind === "field") {
    if (Object.keys(extractFieldErrors(error)).length > 0) return null;
    // details.fields가 비어 폼 인라인이 뜰 자리가 없는 경우에만 배너로 폴백한다.
    // 이때는 서버 message가 taxonomy 문구보다 우선이다(다른 갈래와 반대 순서) —
    // getApiErrorMessage는 알려진 코드면 항상 EXACT_MESSAGES를 우선하므로, 그
    // 우선순위를 여기서 뒤집어야 서버가 보낸 구체적인 사유가 묻히지 않는다.
    const fallbackText = message || getApiErrorMessage(errorCode);
    return (
      <Alert tone="danger">
        <p>{fallbackText}</p>
      </Alert>
    );
  }

  const text = getApiErrorMessage(errorCode, message);

  return (
    <Alert tone="danger">
      <p>{text}</p>
      {kind === "disclosure_retired" && onReload && (
        <div className="mt-2">
          <Button size="sm" variant="secondary" onClick={onReload}>
            최신 내용 다시 불러오기
          </Button>
        </div>
      )}
      {kind === "mfa_invalid" && onFocusMfaCode && (
        <div className="mt-2">
          <Button size="sm" variant="secondary" onClick={onFocusMfaCode}>
            새 코드 입력
          </Button>
        </div>
      )}
    </Alert>
  );
}
