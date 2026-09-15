import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { OfflineBanner } from "./OfflineBanner";

function setNavigatorOnLine(value: boolean) {
  Object.defineProperty(navigator, "onLine", { value, configurable: true });
}

afterEach(() => {
  cleanup();
  setNavigatorOnLine(true);
});

describe("OfflineBanner", () => {
  it("온라인 상태에서는 아무것도 렌더링하지 않는다", () => {
    setNavigatorOnLine(true);
    const { container } = render(<OfflineBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("negative: 오프라인으로 전환되면 배너가 나타나고, 다시 온라인이 되면 사라진다", () => {
    setNavigatorOnLine(true);
    render(<OfflineBanner />);

    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(
      screen.getByText("오프라인 상태입니다. 최근에 불러온 화면만 볼 수 있고 일부 기능이 제한됩니다."),
    ).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(screen.queryByText(/오프라인 상태입니다/)).not.toBeInTheDocument();
  });
});
