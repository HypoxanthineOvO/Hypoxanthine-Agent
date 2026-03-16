import { describe, expect, it, vi } from "vitest";

import { registerHotkeys } from "../useHotkey";

describe("useHotkey", () => {
  it("triggers handler for enter without modifiers", () => {
    const handler = vi.fn();
    const cleanup = registerHotkeys([
      {
        combo: "enter",
        handler,
      },
    ]);

    window.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Enter",
      }),
    );

    expect(handler).toHaveBeenCalledTimes(1);
    cleanup();
  });

  it("does not trigger enter hotkey when modifiers are pressed", () => {
    const handler = vi.fn();
    const cleanup = registerHotkeys([
      {
        combo: "enter",
        handler,
      },
    ]);

    window.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Enter",
        ctrlKey: true,
      }),
    );

    expect(handler).not.toHaveBeenCalled();
    cleanup();
  });

  it("does not trigger after cleanup", () => {
    const handler = vi.fn();
    const cleanup = registerHotkeys([
      {
        combo: "ctrlOrMeta+k",
        handler,
      },
    ]);

    cleanup();
    window.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "k",
        ctrlKey: true,
      }),
    );

    expect(handler).not.toHaveBeenCalled();
  });
});
