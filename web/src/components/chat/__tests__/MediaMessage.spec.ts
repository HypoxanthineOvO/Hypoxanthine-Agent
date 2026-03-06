import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import MediaMessage from "../MediaMessage.vue";

describe("MediaMessage", () => {
  it("renders image for image extension", () => {
    const wrapper = mount(MediaMessage, {
      props: { src: "http://localhost/file.png" },
    });

    expect(wrapper.find("img").exists()).toBe(true);
    expect(wrapper.find("video").exists()).toBe(false);
  });

  it("renders video for video extension", () => {
    const wrapper = mount(MediaMessage, {
      props: { src: "http://localhost/file.mp4" },
    });

    expect(wrapper.find("video").exists()).toBe(true);
    expect(wrapper.find("img").exists()).toBe(false);
  });

  it("falls back to link for unknown extension", () => {
    const wrapper = mount(MediaMessage, {
      props: { src: "http://localhost/file.bin" },
    });

    expect(wrapper.find("a").exists()).toBe(true);
  });
});
