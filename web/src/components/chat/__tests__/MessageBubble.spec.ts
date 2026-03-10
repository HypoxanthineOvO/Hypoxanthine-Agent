import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import MessageBubble from "../MessageBubble.vue";

describe("MessageBubble", () => {
  it("marks reminder message with tag metadata", () => {
    const wrapper = mount(MessageBubble, {
      props: {
        message: {
          text: "提醒：喝水",
          sender: "assistant",
          session_id: "main",
          message_tag: "reminder",
        },
      },
      slots: {
        default: "<div>提醒内容</div>",
      },
    });

    const article = wrapper.find("article.message-bubble");
    expect(article.attributes("data-message-tag")).toBe("reminder");
    expect(article.text()).toContain("提醒内容");
  });
});
