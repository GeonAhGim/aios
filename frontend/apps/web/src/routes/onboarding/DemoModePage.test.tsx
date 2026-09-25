import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { vi } from "vitest";
import { DEMO_INSTRUMENTS } from "./demoDataset";
import { DemoModePage } from "./DemoModePage";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(cleanup);

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/onboarding/demo"]}>
      <DemoModePage />
    </MemoryRouter>,
  );
}

describe("DemoModePage", () => {
  it("고정 샘플 종목 3개를 모두 나열하고 각각 데모 시작 링크를 갖는다", () => {
    renderPage();
    for (const instrument of DEMO_INSTRUMENTS) {
      const link = screen.getByTestId(`demo-start-${instrument.id}`).closest("a");
      expect(link).toHaveAttribute("href", `/onboarding/demo/${instrument.id}`);
    }
  });

  it("negative: 목록 이외의 종목으로는 링크를 만들지 않는다", () => {
    renderPage();
    expect(screen.queryByTestId("demo-start-NOT-A-DEMO-SYMBOL")).not.toBeInTheDocument();
  });
});
