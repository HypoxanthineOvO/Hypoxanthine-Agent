import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ConfigView from "../ConfigView.vue";

async function flushUi(): Promise<void> {
  await Promise.resolve();
  await nextTick();
}

const configFiles = [
  {
    filename: "persona.yaml",
    label: "人设配置",
    icon: "🎭",
    description: "Agent 的性格、语气、系统提示词",
  },
  {
    filename: "skills.yaml",
    label: "技能配置",
    icon: "🔧",
    description: "各技能开关与参数",
  },
  {
    filename: "tasks.yaml",
    label: "定时任务",
    icon: "⏰",
    description: "Heartbeat、邮件扫描等定时任务配置",
  },
  {
    filename: "narration.yaml",
    label: "旁白配置",
    icon: "💬",
    description: "工具调用旁白的开关、模型、分级",
  },
  {
    filename: "email_rules.yaml",
    label: "邮件规则",
    icon: "📧",
    description: "邮件分类硬规则 + LLM 偏好",
  },
  {
    filename: "secrets.yaml",
    label: "密钥配置",
    icon: "🔐",
    description: "API Token、密码等敏感配置（脱敏显示）",
  },
  {
    filename: "security.yaml",
    label: "安全白名单",
    icon: "🛡️",
    description: "文件系统访问白名单",
  },
];

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ConfigView", () => {
  it("loads config list, renders tasks cards, and saves structured data", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/config?token=test-token")) {
        return {
          ok: true,
          json: async () => configFiles,
        };
      }
      if (url.endsWith("/config/persona.yaml?token=test-token")) {
        return {
          ok: true,
          json: async () => ({
            filename: "persona.yaml",
            content: "name: Hypo\naliases: []\npersonality: []\nspeaking_style: {}\nsystem_prompt_template: test",
            data: {
              name: "Hypo",
              aliases: [],
              personality: [],
              speaking_style: {},
              system_prompt_template: "test",
            },
            masked_fields: [],
          }),
        };
      }
      if (url.endsWith("/config/tasks.yaml?token=test-token") && init?.method !== "PUT") {
        return {
          ok: true,
          json: async () => ({
            filename: "tasks.yaml",
            content:
              "heartbeat:\n  enabled: true\n  interval_minutes: 30\n  prompt_template: hello\nemail_scan:\n  enabled: true\n  interval_minutes: 60\nemail_store:\n  enabled: true\n  max_entries: 5000\n  retention_days: 90\n  warmup_hours: 168\n",
            data: {
              heartbeat: {
                enabled: true,
                interval_minutes: 30,
                prompt_template: "hello",
              },
              email_scan: {
                enabled: true,
                interval_minutes: 60,
              },
              email_store: {
                enabled: true,
                max_entries: 5000,
                retention_days: 90,
                warmup_hours: 168,
              },
            },
            masked_fields: [],
          }),
        };
      }
      if (url.endsWith("/config/tasks.yaml?token=test-token") && init?.method === "PUT") {
        return {
          ok: true,
          json: async () => ({
            filename: "tasks.yaml",
            content: "",
            data: {
              heartbeat: {
                enabled: true,
                interval_minutes: 30,
                prompt_template: "hello",
              },
              email_scan: {
                enabled: true,
                interval_minutes: 60,
              },
              email_store: {
                enabled: true,
                max_entries: 5000,
                retention_days: 90,
                warmup_hours: 168,
              },
            },
            masked_fields: [],
            reloaded: true,
          }),
        };
      }
      return {
        ok: true,
        json: async () => ({}),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ConfigView, {
      props: {
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
      global: {
        stubs: {
          MonacoEditor: {
            props: ["modelValue"],
            template: "<textarea data-testid='raw-yaml-editor' :value='modelValue'></textarea>",
          },
        },
      },
    });

    await flushUi();
    await flushUi();
    await flushUi();

    expect(wrapper.text()).toContain("人设配置");
    expect(wrapper.text()).toContain("密钥配置");

    await wrapper.get('[data-testid="config-file-item-tasks.yaml"]').trigger("click");
    await flushUi();
    await flushUi();

    expect(wrapper.get('[data-testid="task-card-heartbeat"]').text()).toContain("Heartbeat");
    expect(wrapper.get('[data-testid="task-card-email_scan"]').text()).toContain("邮件扫描");
    expect(wrapper.get('[data-testid="task-card-email_store"]').text()).toContain("邮件缓存");

    await wrapper.get('[data-testid="config-save"]').trigger("click");
    await flushUi();

    const putCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url) === "http://localhost:8000/api/config/tasks.yaml?token=test-token" &&
        (init as RequestInit | undefined)?.method === "PUT",
    );
    expect(putCall).toBeTruthy();
    const body = JSON.parse(String((putCall?.[1] as RequestInit).body));
    expect(body.data.heartbeat.interval_minutes).toBe(30);
  });

  it("renders masked secrets fields and supports switching to raw yaml mode", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/config?token=test-token")) {
        return {
          ok: true,
          json: async () => configFiles,
        };
      }
      if (url.endsWith("/config/persona.yaml?token=test-token")) {
        return {
          ok: true,
          json: async () => ({
            filename: "persona.yaml",
            content: "name: Hypo\naliases: []\npersonality: []\nspeaking_style: {}\nsystem_prompt_template: test",
            data: {
              name: "Hypo",
              aliases: [],
              personality: [],
              speaking_style: {},
              system_prompt_template: "test",
            },
            masked_fields: [],
          }),
        };
      }
      if (url.endsWith("/config/secrets.yaml?token=test-token")) {
        return {
          ok: true,
          json: async () => ({
            filename: "secrets.yaml",
            content: "providers:\n  Hiapi:\n    api_key: ••••••••\n",
            data: {
              providers: {
                Hiapi: {
                  api_base: "https://hiapi.example/v1",
                  api_key: "••••••••",
                },
              },
              services: {
                qq: {
                  napcat_ws_url: "ws://127.0.0.1:3009/onebot/v11/ws",
                  napcat_ws_token: "••••••••",
                  napcat_http_url: "http://127.0.0.1:3000",
                  napcat_http_token: "••••••••",
                  bot_qq: "123456789",
                  allowed_users: ["10001"],
                },
              },
            },
            masked_fields: [
              "providers.Hiapi.api_key",
              "services.qq.napcat_ws_token",
              "services.qq.napcat_http_token",
            ],
          }),
        };
      }
      return {
        ok: true,
        json: async () => ({}),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ConfigView, {
      props: {
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
      global: {
        stubs: {
          MonacoEditor: {
            props: ["modelValue"],
            template: "<textarea data-testid='raw-yaml-editor' :value='modelValue'></textarea>",
          },
        },
      },
    });

    await flushUi();
    await flushUi();
    await flushUi();

    await wrapper.get('[data-testid="config-file-item-secrets.yaml"]').trigger("click");
    await flushUi();
    await flushUi();

    expect(
      wrapper.find('[data-testid="config-field-providers-Hiapi-api_key"]').exists(),
    ).toBe(true);
    expect(wrapper.html()).toContain("••••••••");
    expect(wrapper.text()).toContain("密钥配置");

    await wrapper.get('[data-testid="toggle-yaml-mode"]').trigger("click");
    await flushUi();

    expect(wrapper.find('[data-testid="raw-yaml-editor"]').exists()).toBe(true);
  });
});
