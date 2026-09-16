import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";

// task-2705 UX-21: 반응형 브레이크포인트 전 화면 점검 + 모바일 레이아웃 회귀.
// AppShell은 모든 화면을 감싸는 셸이라, 여기서 nav 44개 항목(main 26 + settings 6
// + admin 12)의 md 미만 모바일 붕괴를 고치면 개별 화면을 하나씩 손대지 않고도
// "전 화면"이 혜택을 받는다. 이 파일은 그 햄버거 토글의 상태 전이를 검증한다
// (AppShell.test.tsx는 §3.5 권한 게이팅만 다루므로 별도 파일로 분리).
const meData = { email: "user@example.com", isPlatformAdmin: false };

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: meData }),
  useLogout: () => vi.fn(),
}));

function tree(initialPath = "/dashboard") {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <AppShell>
        <div>PAGE_CONTENT</div>
      </AppShell>
    </MemoryRouter>
  );
}

// MemoryRouter는 마운트 후 initialEntries prop이 바뀌어도 내부 history를 다시
// 만들지 않는다(defaultValue와 같은 1회성 prop) — 그래서 "뒤로가기 등으로 경로가
// 바뀐다"를 재현하려면 실제 라우터를 통해 navigate()를 호출해야 한다. children으로
// 이 버튼을 넣어 nav 링크의 onClick 핸들러가 아니라 useEffect(location.pathname)
// 자체가 메뉴를 닫는지 독립적으로 검증한다.
function NavigateAwayButton() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate("/portfolio")}>
      go-elsewhere
    </button>
  );
}

function menuToggle() {
  return screen.getByRole("button", { name: /메뉴/ });
}

afterEach(() => {
  cleanup();
});

describe("AppShell — 모바일 nav 토글(task-2705 UX-21)", () => {
  it("기본 상태에서는 닫혀 있다(aria-expanded=false, 열기 라벨)", () => {
    render(tree());

    const toggle = menuToggle();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAccessibleName("메뉴 열기");
  });

  it("토글을 누르면 열리고 aria-expanded와 라벨이 함께 전환된다", () => {
    render(tree());

    fireEvent.click(menuToggle());

    const toggle = menuToggle();
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveAccessibleName("메뉴 닫기");
  });

  it("negative: 다시 누르면 닫힌 상태로 되돌아간다(토글이 한쪽으로만 굳지 않는다)", () => {
    render(tree());

    fireEvent.click(menuToggle());
    fireEvent.click(menuToggle());

    expect(menuToggle()).toHaveAttribute("aria-expanded", "false");
  });

  it("negative(회귀): 열린 채로 nav 링크를 누르면 메뉴가 닫힌다 — 다음 화면 위에 오버레이가 남지 않는다", () => {
    render(tree());

    fireEvent.click(menuToggle());
    expect(menuToggle()).toHaveAttribute("aria-expanded", "true");

    const dashboardLink = screen.getAllByRole("link").find((el) => el.getAttribute("href") === "/exchanges");
    expect(dashboardLink).toBeDefined();
    fireEvent.click(dashboardLink as HTMLElement);

    expect(menuToggle()).toHaveAttribute("aria-expanded", "false");
  });

  it("negative(회귀): nav 바깥(페이지 콘텐츠)에서 프로그램적으로 경로가 바뀌어도 메뉴가 닫힌다(onNavigate 핸들러가 아니라 경로 변화 자체에 반응)", () => {
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <AppShell>
          <NavigateAwayButton />
        </AppShell>
      </MemoryRouter>,
    );

    fireEvent.click(menuToggle());
    expect(menuToggle()).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(screen.getByRole("button", { name: "go-elsewhere" }));

    expect(menuToggle()).toHaveAttribute("aria-expanded", "false");
  });

  it("토글 버튼은 aria-controls로 nav 패널을 가리킨다", () => {
    render(tree());

    const toggle = menuToggle();
    const controlledId = toggle.getAttribute("aria-controls");
    expect(controlledId).toBeTruthy();
    expect(document.getElementById(controlledId as string)?.tagName).toBe("NAV");
  });
});
