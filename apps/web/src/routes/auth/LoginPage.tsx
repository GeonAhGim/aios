import { useLogin } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { Alert, Button, Field, Input } from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AuthLayout } from "./AuthLayout";

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const login = useLogin();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await login.mutateAsync({ email, password, totpCode: totpCode || undefined });
      navigate("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "로그인에 실패했습니다.");
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
        {error && <Alert>{error}</Alert>}
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
