import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PreviewCondition } from "@aios/shared-types";
import { ConditionGroup } from "./ConditionGroup";

afterEach(() => cleanup());

const INDICATORS = ["RSI", "MACD"];

const CONDITION: PreviewCondition = {
  indicator: "RSI",
  params: { timeperiod: 14 },
  operator: "<",
  threshold: 30,
};

describe("ConditionGroup 렌더링", () => {
  it("title을 보여주고 conditions 개수만큼 행을 그린다", () => {
    render(
      <ConditionGroup
        title="진입 조건"
        conditions={[CONDITION, { ...CONDITION, indicator: "MACD" }]}
        combine="AND"
        onConditionsChange={vi.fn()}
        onCombineChange={vi.fn()}
        indicators={INDICATORS}
      />,
    );

    expect(screen.getByText("진입 조건")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "삭제" })).toHaveLength(2);
  });

  it("조건이 1개 이하면 combine 선택 드롭다운을 보여주지 않는다", () => {
    render(
      <ConditionGroup
        title="진입 조건"
        conditions={[CONDITION]}
        combine="AND"
        onConditionsChange={vi.fn()}
        onCombineChange={vi.fn()}
        indicators={INDICATORS}
      />,
    );

    expect(screen.queryByText("모두 만족(AND)")).not.toBeInTheDocument();
  });

  it("조건이 2개 이상이면 combine 선택 드롭다운을 보여준다", () => {
    render(
      <ConditionGroup
        title="진입 조건"
        conditions={[CONDITION, { ...CONDITION, indicator: "MACD" }]}
        combine="OR"
        onConditionsChange={vi.fn()}
        onCombineChange={vi.fn()}
        indicators={INDICATORS}
      />,
    );

    expect(screen.getByText("모두 만족(AND)")).toBeInTheDocument();
    expect(screen.getByText("하나라도 만족(OR)")).toBeInTheDocument();
  });
});

describe("ConditionGroup 조건 추가·수정·삭제", () => {
  it("조건 추가 클릭 시 기본 조건이 추가된 배열로 onConditionsChange를 호출한다", () => {
    const onConditionsChange = vi.fn();
    render(
      <ConditionGroup
        title="진입 조건"
        conditions={[CONDITION]}
        combine="AND"
        onConditionsChange={onConditionsChange}
        onCombineChange={vi.fn()}
        indicators={INDICATORS}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "+ 조건 추가" }));

    expect(onConditionsChange).toHaveBeenCalledWith([
      CONDITION,
      { indicator: "RSI", params: { timeperiod: 14 }, operator: "<", threshold: 30 },
    ]);
  });

  it("한 행을 수정하면 해당 인덱스만 바뀐 배열로 onConditionsChange를 호출한다", () => {
    const onConditionsChange = vi.fn();
    const second = { ...CONDITION, indicator: "MACD" };
    render(
      <ConditionGroup
        title="진입 조건"
        conditions={[CONDITION, second]}
        combine="AND"
        onConditionsChange={onConditionsChange}
        onCombineChange={vi.fn()}
        indicators={INDICATORS}
      />,
    );

    fireEvent.change(screen.getAllByPlaceholderText("임계값")[0], { target: { value: "50" } });

    expect(onConditionsChange).toHaveBeenCalledWith([{ ...CONDITION, threshold: 50 }, second]);
  });

  it("한 행을 삭제하면 나머지 행만 남은 배열로 onConditionsChange를 호출한다", () => {
    const onConditionsChange = vi.fn();
    const second = { ...CONDITION, indicator: "MACD" };
    render(
      <ConditionGroup
        title="진입 조건"
        conditions={[CONDITION, second]}
        combine="AND"
        onConditionsChange={onConditionsChange}
        onCombineChange={vi.fn()}
        indicators={INDICATORS}
      />,
    );

    fireEvent.click(screen.getAllByRole("button", { name: "삭제" })[0]);

    expect(onConditionsChange).toHaveBeenCalledWith([second]);
  });

  it("combine을 바꾸면 onCombineChange를 호출한다", () => {
    const onCombineChange = vi.fn();
    render(
      <ConditionGroup
        title="진입 조건"
        conditions={[CONDITION, { ...CONDITION, indicator: "MACD" }]}
        combine="AND"
        onConditionsChange={vi.fn()}
        onCombineChange={onCombineChange}
        indicators={INDICATORS}
      />,
    );

    const [combineSelect] = screen.getAllByRole("combobox");
    fireEvent.change(combineSelect, { target: { value: "OR" } });

    expect(onCombineChange).toHaveBeenCalledWith("OR");
  });
});
