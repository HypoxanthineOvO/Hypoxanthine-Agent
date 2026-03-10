import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import CodeBlock from "../CodeBlock.vue";

describe("CodeBlock", () => {
  it("renders language label and line numbers", () => {
    const wrapper = mount(CodeBlock, {
      props: {
        code: "const a = 1;\nconst b = 2;",
        language: "ts",
      },
    });

    expect(wrapper.text()).toContain("TS");
    expect(wrapper.findAll(".line-number")).toHaveLength(2);
  });

  it("copies content to clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", {
      clipboard: { writeText },
    });

    const wrapper = mount(CodeBlock, {
      props: {
        code: "print('ok')",
        language: "py",
      },
    });

    await wrapper.get('[data-testid="copy-code-button"]').trigger("click");
    expect(writeText).toHaveBeenCalledWith("print('ok')");
  });
});
