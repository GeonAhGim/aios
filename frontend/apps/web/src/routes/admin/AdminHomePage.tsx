import { PageHeader } from "@aios/ui-web";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";

const SECTIONS = [
  { to: "/admin/verification-queue", label: "전략 검수 대기열", desc: "FD-13.2 수동 검증" },
  { to: "/admin/disputes", label: "분쟁 관리", desc: "FD-13.6 분쟁 조정" },
  { to: "/admin/users", label: "사용자 관리", desc: "FD-18.2/18.4 상태변경·판매정지" },
  { to: "/admin/wallet-topups", label: "충전 요청 대기 목록", desc: "FD-13.11 입금 확인" },
  {
    to: "/admin/marketplace/platform-listings",
    label: "플랫폼 전략 등록",
    desc: "하우스 계정 직접판매(B2C)",
  },
  { to: "/admin/approval-requests", label: "승인 요청 처리", desc: "FD-10.1/9.4b" },
];

// task-1155: 이 화면 자체는 조회·변경 API 호출이 없는 정적 내비게이션 허브라
// 표면화할 에러 경로가 없다 — 진입 시 실제로 실패할 수 있는 지점은 이 라우트를
// 감싸는 AdminRoute(useMe())뿐이며, 그 isError 분기를 여기서 함께 고쳤다.
export function AdminHomePage() {
  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="관리자 도구" />
        <div className="grid grid-cols-2 gap-4">
          {SECTIONS.map((s) => (
            <Link
              key={s.to}
              to={s.to}
              className="rounded-lg border border-border bg-surface p-4 transition-colors hover:border-border-strong hover:bg-surface-hover"
            >
              <p className="font-medium text-fg">{s.label}</p>
              <p className="text-sm text-fg-muted">{s.desc}</p>
            </Link>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
