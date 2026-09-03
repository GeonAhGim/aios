import { useSignup } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyBadRequest, classifyForbidden, deriveLockout, routeApiError } from "@aios/shared-types";
import { Button, Field, Input } from "@aios/ui-web";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { AuthLayout } from "./AuthLayout";

// spec §3.3 에러 taxonomy: 회원가입 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 400/403/그 외를 각각 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-902). 423
// AUTH_ACCOUNT_LOCKED은 LoginPage(task-387)와 동일하게 deriveLockout으로
// 잠금 카운트다운을 재사용한다 — 새 분류기는 만들지 않는다.
function SignupError({ error }: { error: unknown }) {
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

export function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [lockoutRemainingSec, setLockoutRemainingSec] = useState(0);
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
    try {
      await signup.mutateAsync({ email, password });
      navigate("/onboarding/mfa-setup");
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error("회원가입에 실패했습니다."));
      setLockoutRemainingSec(deriveLockout(err).retryAfterSec);
    }
  }

  return (
    <AuthLayout title="AIOS 회원가입" subtitle="자동매매를 시작하기 위한 첫 단계입니다">
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="이메일" htmlFor="email">
          <Input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            disabled={locked}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field label="비밀번호" htmlFor="password" hint="12자 이상">
          <Input
            id="password"
            type="password"
            required
            minLength={12}
            autoComplete="new-password"
            value={password}
            disabled={locked}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        {error !== null && <SignupError error={error} />}
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
