import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import PipelineProgress from "../PipelineProgress.vue";

describe("PipelineProgress", () => {
  it("filters preprocessing items from rendering", () => {
    const wrapper = mount(PipelineProgress, {
      props: {
        message: {
          sender: "assistant",
          session_id: "main",
          text: "正在检索相关记忆...",
          metadata: {
            pipeline_items: [
              {
                event_type: "pipeline_stage",
                text: "正在分析你的消息...",
                stage: "preprocessing",
                status: "running",
              },
              {
                event_type: "pipeline_stage",
                text: "正在检索相关记忆...",
                stage: "memory_injection",
                status: "running",
              },
            ],
          },
        },
      },
    });

    expect(wrapper.text()).not.toContain("正在分析你的消息");
    expect(wrapper.text()).toContain("正在检索相关记忆");
  });
});
