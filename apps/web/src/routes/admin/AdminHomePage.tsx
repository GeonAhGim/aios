import { Link } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";

const SECTIONS = [
  { to: "/admin/verification-queue", label: "전략 검수 대기열", desc: "FD-13.2 수동 검증" },
  { to: "/admin/disputes", label: "분쟁 관리", desc: "FD-13.6 분쟁 조정" },
  { to: "/admin/users", label: "사용자 관리", desc: "FD-18.2/18.4 상태변경·판매정지" },
  { to: "/admin/pending-payments", label: "결제 대기 목록", desc: "FD-18.5a/b 입금 확인" },
  { to: "/admin/approval-requests", label: "승인 요청 처리", desc: "FD-10.1/9.4b" },
];

export function AdminHomePage() {
  return (
    <AppShell>
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold text-slate-100">관리자 도구</h1>
        <div className="grid grid-cols-2 gap-4">
          {SECTIONS.map((s) => (
            <Link
              key={s.to}
              to={s.to}
              className="rounded-lg border border-slate-800 p-4 hover:border-slate-600"
            >
              <p className="font-medium text-slate-100">{s.label}</p>
              <p className="text-sm text-slate-500">{s.desc}</p>
            </Link>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
