import { useSignup } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { Alert, Button, Field, Input } from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AuthLayout } from "./AuthLayout";

export function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const signup = useSignup();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await signup.mutateAsync({ email, password });
      navigate("/onboarding/mfa-setup");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "회원가입에 실패했습니다.");
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
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        {error && <Alert>{error}</Alert>}
        <Button type="submit" loading={signup.isPending} className="w-full">
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
