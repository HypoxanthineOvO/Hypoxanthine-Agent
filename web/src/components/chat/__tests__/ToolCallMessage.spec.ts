import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ToolCallMessage from "../ToolCallMessage.vue";

describe("ToolCallMessage", () => {
  it("renders one-line summary by default", () => {
    const wrapper = mount(ToolCallMessage, {
      props: {
        toolName: "run_command",
        status: "success",
        params: { command: "nvidia-smi" },
      },
    });

    expect(wrapper.text()).toContain('🔧 执行了 run_command({"command":"nvidia-smi"}) → 成功');
    expect(wrapper.find(".details").exists()).toBe(false);
  });

  it("expands details when clicked", async () => {
    const wrapper = mount(ToolCallMessage, {
      props: {
        toolName: "run_command",
        status: "error",
        params: { command: "bad" },
        result: { stderr: "oops" },
      },
    });

    await wrapper.get(".summary-button").trigger("click");
    expect(wrapper.find(".details").exists()).toBe(true);
    expect(wrapper.text()).toContain("参数");
    expect(wrapper.text()).toContain("输出");
    expect(wrapper.text()).toContain("oops");
  });
});
