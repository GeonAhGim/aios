import { useSignup } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyBadRequest, classifyForbidden, deriveLockout, routeApiError } from "@aios/shared-types";
import { Button, Field, Input } from "@aios/ui-web";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { useFieldErrors } from "../../hooks/useFieldErrors";
import { AuthLayout } from "./AuthLayout";

// spec §3.3 에러 taxonomy: 회원가입 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 400/403/그 외를 각각 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-902). 423
// AUTH_ACCOUNT_LOCKED은 LoginPage(task-387)와 동일하게 deriveLockout으로
// 잠금 카운트다운을 재사용한다 — 새 분류기는 만들지 않는다.
//
// task-943: VALIDATION_INVALID_FIELD는 classifyBadRequest가 "field"로 분류해
// BadRequestNotice가 자체적으로 null을 렌더한다(task-364 설계) — 그래서 지금까지
// 이 경로는 배너도 인라인도 없이 완전히 조용했다. fieldErrors를 ErrorMessage에
// 넘겨 계약(비어있지 않으면 배너 생략)을 지키고, 실제 표시는 아래 입력 옆
// Field.error로 한다.
function SignupError({ error, fieldErrors }: { error: unknown; fieldErrors: Record<string, string> }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      fieldErrors={fieldErrors}
    />
  );
}

export function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [lockoutRemainingSec, setLockoutRemainingSec] = useState(0);
  const { fieldErrors, setFromError, clearField } = useFieldErrors();
  const signup = useSignup();
  const navigate = useNavigate();
  const locked = lockoutRemainingSec > 0;

  useEffect(() => {
    if (lockoutRemainingSec <= 0) return;
    const timer = setTimeout(() => setLockoutRemainingSec((sec) => Math.max(0, sec - 1)), 1000);
    return () => clearTimeout(timer);
  }, [lockoutRemainingSec]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (locked) return;
    setError(null);
    setFromError(null);
    try {
      await signup.mutateAsync({ email, password });
      navigate("/onboarding/mfa-setup");
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error("회원가입에 실패했습니다."));
      setFromError(err);
      setLockoutRemainingSec(deriveLockout(err).retryAfterSec);
    }
  }

  return (
    <AuthLayout title="AIOS 회원가입" subtitle="자동매매를 시작하기 위한 첫 단계입니다">
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="이메일" htmlFor="email" error={fieldErrors.email}>
          <Input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            disabled={locked}
            onChange={(e) => {
              setEmail(e.target.value);
              clearField("email");
            }}
          />
        </Field>
        <Field label="비밀번호" htmlFor="password" hint="12자 이상" error={fieldErrors.password}>
          <Input
            id="password"
            type="password"
            required
            minLength={12}
            autoComplete="new-password"
            value={password}
            disabled={locked}
            onChange={(e) => {
              setPassword(e.target.value);
              clearField("password");
            }}
          />
        </Field>
        {error !== null && <SignupError error={error} fieldErrors={fieldErrors} />}
        {locked && (
          <p role="status" className="text-sm text-fg-muted">
            {lockoutRemainingSec}초 후 다시 시도할 수 있습니다.
          </p>
        )}
        <Button type="submit" loading={signup.isPending} disabled={locked} className="w-full">
          가입하기
        </Button>
        <p className="text-center text-sm text-fg-muted">
          이미 계정이 있으신가요?{" "}
          <Link to="/login" className="text-accent-hover hover:underline">
            로그인
          </Link>
        </p>
      </form>
    </AuthLayout>
  );
}
