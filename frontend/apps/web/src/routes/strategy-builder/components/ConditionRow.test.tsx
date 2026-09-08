import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PreviewCondition } from "@aios/shared-types";
import { ConditionRow } from "./ConditionRow";

afterEach(() => cleanup());

const CONDITION: PreviewCondition = {
  indicator: "RSI",
  params: { timeperiod: 14 },
  operator: "<",
  threshold: 30,
};

const INDICATORS = ["RSI", "MACD", "SMA"];

function selects(): HTMLSelectElement[] {
  return screen.getAllByRole("combobox") as HTMLSelectElement[];
}

describe("ConditionRow 값 렌더링", () => {
  it("indicator·params.timeperiod·operator·threshold 값을 각 입력에 반영한다", () => {
    render(
      <ConditionRow value={CONDITION} onChange={vi.fn()} onRemove={vi.fn()} indicators={INDICATORS} />,
    );

    const [indicatorSelect, operatorSelect] = selects();
    expect(indicatorSelect.value).toBe("RSI");
    expect(operatorSelect.value).toBe("<");
    expect(screen.getByPlaceholderText("period")).toHaveValue(14);
    expect(screen.getByPlaceholderText("임계값")).toHaveValue(30);
  });

  it("indicators 목록으로 select 옵션을 만든다", () => {
    render(
      <ConditionRow value={CONDITION} onChange={vi.fn()} onRemove={vi.fn()} indicators={INDICATORS} />,
    );

    const [indicatorSelect] = selects();
    expect([...indicatorSelect.options].map((o) => o.value)).toEqual(INDICATORS);
  });
});

describe("ConditionRow 값 변경", () => {
  it("지표를 바꾸면 onChange({...value, indicator})를 호출한다", () => {
    const onChange = vi.fn();
    render(
      <ConditionRow value={CONDITION} onChange={onChange} onRemove={vi.fn()} indicators={INDICATORS} />,
    );

    fireEvent.change(selects()[0], { target: { value: "MACD" } });

    expect(onChange).toHaveBeenCalledWith({ ...CONDITION, indicator: "MACD" });
  });

  it("period를 비우면 params를 빈 객체로 바꾼다", () => {
    const onChange = vi.fn();
    render(
      <ConditionRow value={CONDITION} onChange={onChange} onRemove={vi.fn()} indicators={INDICATORS} />,
    );

    fireEvent.change(screen.getByPlaceholderText("period"), { target: { value: "" } });

    expect(onChange).toHaveBeenCalledWith({ ...CONDITION, params: {} });
  });

  it("period를 숫자로 바꾸면 params.timeperiod를 숫자로 반영한다", () => {
    const onChange = vi.fn();
    render(
      <ConditionRow value={CONDITION} onChange={onChange} onRemove={vi.fn()} indicators={INDICATORS} />,
    );

    fireEvent.change(screen.getByPlaceholderText("period"), { target: { value: "20" } });

    expect(onChange).toHaveBeenCalledWith({ ...CONDITION, params: { timeperiod: 20 } });
  });

  it("연산자를 바꾸면 onChange({...value, operator})를 호출한다", () => {
    const onChange = vi.fn();
    render(
      <ConditionRow value={CONDITION} onChange={onChange} onRemove={vi.fn()} indicators={INDICATORS} />,
    );

    fireEvent.change(selects()[1], { target: { value: "crosses_above" } });

    expect(onChange).toHaveBeenCalledWith({ ...CONDITION, operator: "crosses_above" });
  });

  it("임계값을 바꾸면 숫자로 변환해 onChange를 호출한다", () => {
    const onChange = vi.fn();
    render(
      <ConditionRow value={CONDITION} onChange={onChange} onRemove={vi.fn()} indicators={INDICATORS} />,
    );

    fireEvent.change(screen.getByPlaceholderText("임계값"), { target: { value: "70" } });

    expect(onChange).toHaveBeenCalledWith({ ...CONDITION, threshold: 70 });
  });

  it("삭제 클릭 시 onRemove를 호출한다", () => {
    const onRemove = vi.fn();
    render(
      <ConditionRow value={CONDITION} onChange={vi.fn()} onRemove={onRemove} indicators={INDICATORS} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "삭제" }));

    expect(onRemove).toHaveBeenCalled();
  });
});
