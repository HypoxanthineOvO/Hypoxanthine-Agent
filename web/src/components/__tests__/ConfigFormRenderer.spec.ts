import { mount } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import type { ComponentPublicInstance } from "vue";
import { describe, expect, it } from "vitest";

import ConfigFormRenderer from "../ConfigFormRenderer.vue";

const passthroughComponent = (tag: string) =>
  defineComponent({
    inheritAttrs: false,
    props: {
      value: { type: null, required: false },
      modelValue: { type: null, required: false },
      type: { type: String, required: false },
      options: { type: Array, required: false },
    },
    emits: ["update:value", "update:modelValue", "click", "close"],
    setup(props, { attrs, emit, slots }) {
      if (tag === "input") {
        return () =>
          h("input", {
            ...attrs,
            type: props.type ?? "text",
            value: props.value ?? props.modelValue ?? "",
            checked: props.type === "checkbox" ? Boolean(props.value ?? props.modelValue) : undefined,
            onInput: (event: Event) =>
              emit("update:value", (event.target as HTMLInputElement).value),
            onChange: (event: Event) =>
              emit(
                "update:value",
                props.type === "checkbox"
                  ? (event.target as HTMLInputElement).checked
                  : (event.target as HTMLInputElement).value,
              ),
            onClick: () => emit("click"),
          });
      }

      if (tag === "select") {
        return () =>
          h(
            "select",
            {
              ...attrs,
              value: props.value ?? props.modelValue ?? "",
              onChange: (event: Event) =>
                emit("update:value", (event.target as HTMLSelectElement).value),
            },
            (props.options ?? []).map((option) =>
              h(
                "option",
                {
                  value: (option as { value: string }).value,
                },
                (option as { label: string }).label,
              ),
            ),
          );
      }

      return () => h("div", attrs, slots.default?.());
    },
  });

interface RendererProps {
  modelValue: unknown;
  maskedFields?: string[];
  path?: string;
  fileName?: string;
}

const componentStubs = {
  NInput: passthroughComponent("input"),
  NInputNumber: passthroughComponent("input"),
  NSelect: passthroughComponent("select"),
  NSwitch: passthroughComponent("input"),
  NButton: passthroughComponent("div"),
  NCard: passthroughComponent("div"),
  NCollapse: passthroughComponent("div"),
  NCollapseItem: passthroughComponent("div"),
  NTag: passthroughComponent("div"),
  "n-input": passthroughComponent("input"),
  "n-input-number": passthroughComponent("input"),
  "n-select": passthroughComponent("select"),
  "n-switch": passthroughComponent("input"),
  "n-button": passthroughComponent("div"),
  "n-card": passthroughComponent("div"),
  "n-collapse": passthroughComponent("div"),
  "n-collapse-item": passthroughComponent("div"),
  "n-tag": passthroughComponent("div"),
};

const mountRenderer = (props: RendererProps) =>
  mount(ConfigFormRenderer, {
    props,
    global: {
      stubs: componentStubs,
    },
  });

describe("ConfigFormRenderer", () => {
  it("renders text, number, select, checkbox and masked fields", () => {
    const wrapper = mountRenderer({
      modelValue: {
        provider: "OpenAI",
        enabled: true,
        retry_count: 3,
        api_key: "secret",
      },
      maskedFields: ["api_key"],
    });

    expect(wrapper.find('[data-testid="config-field-provider"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="config-field-enabled"]').attributes("role")).toBe("switch");
    expect(wrapper.get('[data-testid="config-field-retry_count"] input').attributes("type")).toBe("text");
    expect(wrapper.get('[data-testid="config-field-api_key"] input').attributes("type")).toBe("password");
  });

  it("emits updated object values when fields change", async () => {
    const wrapper = mountRenderer({
      modelValue: {
        username: "demo",
        enabled: false,
      },
    });

    await wrapper.get('[data-testid="config-field-username"] input').setValue("alice");
    await wrapper.get('[data-testid="config-field-enabled"]').trigger("click");

    const emissions = wrapper.emitted("update:modelValue") ?? [];
    expect(emissions).toHaveLength(2);
    expect(emissions[0]?.[0]).toMatchObject({ username: "alice", enabled: false });
    expect(emissions[1]?.[0]).toMatchObject({ username: "demo", enabled: true });
  });

  it("resets litellm_model when provider changes", async () => {
    const wrapper = mountRenderer({
      modelValue: {
        provider: "OpenAI",
        litellm_model: "gpt-4o",
      },
    });

    (
      wrapper.getComponent('[data-testid="config-field-provider"]') as VueWrapper<ComponentPublicInstance>
    ).vm.$emit("update:value", "Anthropic");

    const emissions = wrapper.emitted("update:modelValue") ?? [];
    expect(emissions.at(-1)?.[0]).toMatchObject({
      provider: "Anthropic",
      litellm_model: "",
    });
  });
});
