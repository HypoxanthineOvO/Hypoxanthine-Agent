import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatMessageTime } from "../timeFormat";

const pad = (value: number): string => String(value).padStart(2, "0");

const localClock = (value: string): string => {
  const date = new Date(value);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

describe("formatMessageTime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("formats today's message as HH:mm", () => {
    vi.setSystemTime(new Date("2026-03-14T12:00:00Z"));
    const input = "2026-03-14T06:30:00Z";

    expect(formatMessageTime(input)).toBe(localClock(input));
  });

  it("formats yesterday's message with 昨天 prefix", () => {
    vi.setSystemTime(new Date("2026-03-14T12:00:00Z"));
    const input = "2026-03-13T06:30:00Z";

    expect(formatMessageTime(input)).toBe(`昨天 ${localClock(input)}`);
  });

  it("formats older messages in the current year as M月D日 HH:mm", () => {
    vi.setSystemTime(new Date("2026-08-20T12:00:00Z"));
    const input = "2026-03-14T06:30:00Z";
    const date = new Date(input);

    expect(formatMessageTime(input)).toBe(
      `${date.getMonth() + 1}月${date.getDate()}日 ${localClock(input)}`,
    );
  });

  it("formats older messages from a previous year with the year", () => {
    vi.setSystemTime(new Date("2026-03-14T12:00:00Z"));
    const input = "2025-12-25T06:30:00Z";
    const date = new Date(input);

    expect(formatMessageTime(input)).toBe(
      `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${localClock(input)}`,
    );
  });
});
