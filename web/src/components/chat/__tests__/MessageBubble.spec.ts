import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import MessageBubble from "../MessageBubble.vue";

describe("MessageBubble", () => {
  it("renders 💓 for heartbeat and 🔔 for reminder", () => {
    const reminder = mount(MessageBubble, {
      props: {
        message: {
          text: "提醒：喝水",
          sender: "assistant",
          session_id: "main",
          message_tag: "reminder",
        },
      },
      slots: { default: "<div>提醒内容</div>" },
    });
    expect(reminder.find("article.message-bubble").attributes("data-message-tag")).toBe(
      "reminder",
    );
    expect(reminder.text()).toContain("🔔 提醒");

    const heartbeat = mount(MessageBubble, {
      props: {
        message: {
          text: "巡检发现异常",
          sender: "assistant",
          session_id: "main",
          message_tag: "heartbeat",
        },
      },
      slots: { default: "<div>巡检内容</div>" },
    });
    expect(heartbeat.find("article.message-bubble").attributes("data-message-tag")).toBe(
      "heartbeat",
    );
    expect(heartbeat.text()).toContain("💓 巡检");
  });

  it("does not render tag for normal message", () => {
    const wrapper = mount(MessageBubble, {
      props: {
        message: {
          text: "普通回复",
          sender: "assistant",
          session_id: "main",
        },
      },
      slots: { default: "<div>普通内容</div>" },
    });

    expect(wrapper.find(".message-tag").exists()).toBe(false);
  });

  it("renders weixin source label for forwarded messages", () => {
    const wrapper = mount(MessageBubble, {
      props: {
        message: {
          text: "来自微信",
          sender: "user",
          session_id: "main",
          channel: "weixin",
        },
      },
      slots: { default: "<div>微信内容</div>" },
    });

    expect(wrapper.text()).toContain("💬 微信");
  });

  it("renders formatted time for normal messages and hides it for narration", () => {
    const normal = mount(MessageBubble, {
      props: {
        message: {
          text: "普通回复",
          sender: "assistant",
          session_id: "main",
          timestamp: "2026-03-14T06:30:00Z",
        },
      },
      slots: { default: "<div>普通内容</div>" },
    });

    expect(normal.find(".bubble-time").exists()).toBe(true);

    const narration = mount(MessageBubble, {
      props: {
        message: {
          text: "我去看一下。",
          sender: "assistant",
          session_id: "main",
          timestamp: "2026-03-14T06:30:00Z",
          message_tag: "narration",
        },
      },
      slots: { default: "<div>旁白内容</div>" },
    });

    expect(narration.find(".bubble-time").exists()).toBe(false);
  });
});
