import { classifyForbidden, getApiErrorMessage } from "@aios/shared-types";
import { Alert, Button } from "@aios/ui-web";
import { DenialReasons } from "./DenialReasons";

// 403 응답을 classifyForbidden(shared-types/forbidden.ts)의 네 갈래로 나눠 보여주는
// 표시 전용 컴포넌트. status가 403이 아니면(또는 error가 비어있으면) null을 반환해
// 기존 ErrorMessage 배너가 계속 담당하게 둔다.
//
// 문구 자체는 apiError.ts의 EXACT_MESSAGES(taxonomy와 1:1)를 그대로 재사용한다 —
// 중복 문구 정의 금지. policy 갈래의 상세 사유는 DenialReasons(task-382)에 위임한다.
// mfa_required일 때만 onStepUp 액션 버튼을 보여준다 — 나머지 갈래는 재시도해도 같은
// 결과가 반복되므로 재시도 버튼을 두지 않는다.
interface ForbiddenNoticeProps {
  error?: unknown;
  onStepUp?: () => void;
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

export function ForbiddenNotice({ error, onStepUp }: ForbiddenNoticeProps) {
  const kind = classifyForbidden(error);
  if (!kind) return null;

  const text = getApiErrorMessage(extractErrorCode(error), extractMessage(error));

  return (
    <Alert tone="danger">
      <p>{text}</p>
      {kind === "policy" && <DenialReasons error={error} />}
      {kind === "mfa_required" && onStepUp && (
        <div className="mt-2">
          <Button size="sm" variant="primary" onClick={onStepUp}>
            step-up 인증
          </Button>
        </div>
      )}
    </Alert>
  );
}
