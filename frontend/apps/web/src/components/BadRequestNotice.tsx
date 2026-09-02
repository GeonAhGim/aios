import { classifyBadRequest, getApiErrorMessage } from "@aios/shared-types";
import { Alert, Button } from "@aios/ui-web";

// 400 응답을 classifyBadRequest(shared-types/badRequest.ts)의 다섯 갈래로 나눠 보여주는
// 표시 전용 컴포넌트. status가 400이 아니면(또는 error가 비어있으면) null을 반환해
// 기존 ErrorMessage 배너가 계속 담당하게 둔다.
//
// field 갈래는 useFieldErrors가 폼 인라인으로 이미 보여주므로(task-364) 이 배너는
// 렌더링하지 않는다(중복 안내 금지) — null을 반환한다.
// 문구 자체는 apiError.ts의 EXACT_MESSAGES(taxonomy와 1:1)를 그대로 재사용한다 —
// 중복 문구 정의 금지. disclosure_retired만 최신 revision 재조회 버튼을,
// mfa_invalid만 코드 재입력 필드로 포커스를 옮기는 콜백 버튼을 보여준다 — 나머지
// 갈래는 재시도해도 같은 결과가 반복되므로 액션 버튼을 두지 않는다.
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
  if (!kind || kind === "field") return null;

  const text = getApiErrorMessage(extractErrorCode(error), extractMessage(error));

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
