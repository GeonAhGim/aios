import { useLogin } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { Button, Field, Input } from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ErrorMessage } from "../../components/ErrorMessage";
import { AuthLayout } from "./AuthLayout";

// task-354: ProtectedRoute가 세션 만료·미로그인 시 남긴 ?next=<원경로>로
// 로그인 성공 후 복귀한다. 외부 사이트로 여는 open-redirect를 막기 위해
// "/"로 시작하되 "//"(프로토콜 상대 URL)는 아닌 경로만 신뢰한다.
function sanitizeNextPath(next: string | null): string {
  if (next && next.startsWith("/") && !next.startsWith("//")) return next;
  return "/dashboard";
}

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState<ApiError | Error | null>(null);
  const login = useLogin();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await login.mutateAsync({ email, password, totpCode: totpCode || undefined });
      navigate(sanitizeNextPath(searchParams.get("next")));
    } catch (err) {
      setError(err instanceof Error ? err : new Error("로그인에 실패했습니다."));
    }
  }

  return (
    <AuthLayout title="AIOS 로그인">
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="이메일" htmlFor="email">
          <Input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field label="비밀번호" htmlFor="password">
          <Input
            id="password"
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <Field label="2단계 인증 코드" htmlFor="totp" hint="설정한 경우만 입력">
          <Input
            id="totp"
            type="text"
            inputMode="numeric"
            value={totpCode}
            onChange={(e) => setTotpCode(e.target.value)}
          />
        </Field>
        {error && <ErrorMessage traceId={error instanceof ApiError ? error.traceId : null} />}
        <Button type="submit" loading={login.isPending} className="w-full">
          로그인
        </Button>
        <p className="text-center text-sm text-fg-muted">
          계정이 없으신가요?{" "}
          <Link to="/signup" className="text-accent-hover hover:underline">
            회원가입
          </Link>
        </p>
      </form>
    </AuthLayout>
  );
}
