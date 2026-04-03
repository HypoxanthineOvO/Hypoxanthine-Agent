import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("vue-echarts", () => ({
  default: {
    name: "VChart",
    props: {
      option: {
        type: Object,
        required: true,
      },
      theme: {
        type: String,
        required: false,
      },
    },
    template: "<div class='chart-stub' :data-theme='theme || \"\"'></div>",
  },
}));

import { flushUi } from "@/test/utils";

import DashboardView from "../DashboardView.vue";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  document.documentElement.dataset.theme = "light";
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.theme;
});

describe("DashboardView", () => {
  const mockDashboardFetch = (
    tokenRows: Array<Record<string, unknown>>,
    latencyModelRows: Array<Record<string, unknown>>,
    recentLatencyRows: Array<Record<string, unknown>> = [],
    channels = {
      channels: {
        webui: {
          status: "connected",
          active_connections: 1,
          last_message_at: new Date().toISOString(),
        },
        qq_bot: {
          status: "connected",
          qq_bot_enabled: true,
          qq_bot_app_id: "••••4756",
          ws_connected: true,
          connected_at: null,
          last_message_at: new Date().toISOString(),
          messages_received: 3,
          messages_sent: 2,
        },
        weixin: {
          status: "connected",
          bot_id: "wx-bot-1",
          user_id: "target@im.wechat",
          last_message_at: new Date().toISOString(),
          messages_received: 12,
          messages_sent: 9,
        },
        feishu: {
          status: "connected",
          app_id: "••••ishu",
          chat_count: 2,
          last_message_at: new Date().toISOString(),
          messages_received: 5,
          messages_sent: 4,
        },
        email: {
          status: "enabled",
          accounts: ["hyx021203@shanghaitech.edu.cn"],
          last_scan_at: new Date().toISOString(),
          next_scan_at: new Date().toISOString(),
          emails_processed: 15,
        },
        heartbeat: {
          status: "running",
          last_heartbeat_at: new Date().toISOString(),
          active_tasks: 2,
        },
      },
    },
  ): ReturnType<typeof vi.fn> => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/dashboard/status?")) {
        return {
          ok: true,
          json: async () => ({
            uptime_seconds: 12,
            uptime_human: "0:00:12",
            session_count: 3,
            kill_switch: false,
            bwrap_available: true,
          }),
        };
      }
      if (url.includes("/dashboard/token-stats?")) {
        return {
          ok: true,
          json: async () => ({ data: tokenRows }),
        };
      }
      if (url.includes("/dashboard/latency-stats?") && url.includes("group_by=model")) {
        return {
          ok: true,
          json: async () => ({ data: latencyModelRows }),
        };
      }
      if (url.includes("/dashboard/recent-latency?")) {
        return {
          ok: true,
          json: async () => ({ data: recentLatencyRows }),
        };
      }
      if (url.includes("/dashboard/recent-tasks?")) {
        return {
          ok: true,
          json: async () => ({
            data: [{ id: 1, created_at: "2026-03-06", tool_name: "run", status: "success", duration_ms: 10 }],
          }),
        };
      }
      if (url.includes("/dashboard/errors/recent?")) {
        return {
          ok: true,
          json: async () => ({
            data: [
              {
                timestamp: "2026-03-06T10:20:30Z",
                level: "error",
                message: "LLM 调用超时",
                detail: "timeout detail",
                source: "hypo_agent.gateway.ws",
              },
            ],
          }),
        };
      }
      if (url.includes("/dashboard/skills?")) {
        return {
          ok: true,
          json: async () => ({ data: [{ name: "exec", status: "healthy", tools: ["exec_command"] }] }),
        };
      }
      if (url.includes("/channels/status?")) {
        return {
          ok: true,
          json: async () => channels,
        };
      }
      if (url.includes("/sessions?")) {
        return {
          ok: true,
          json: async () => [],
        };
      }
      return {
        ok: true,
        json: async () => ({}),
      };
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  };

  it("loads dashboard data with tokenized requests", async () => {
    const fetchMock = mockDashboardFetch(
      [{ date: "2026-03-06", model: "Gemini3Pro", total_tokens: 100 }],
      [{ date: "2026-03-06", p50_ms: 50, p95_ms: 80, p99_ms: 120 }],
    );

    const wrapper = mount(DashboardView, {
      props: {
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
    });

    await flushUi();
    await flushUi();
    await flushUi();

    expect(
      fetchMock.mock.calls.some(
        ([url]) => String(url) === "http://localhost:8000/api/dashboard/status?token=test-token",
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(
        ([url]) => String(url) === "http://localhost:8000/api/channels/status?token=test-token",
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(
        ([url]) =>
          String(url) ===
          "http://localhost:8000/api/dashboard/recent-latency?limit=24&token=test-token",
      ),
    ).toBe(true);
    expect(wrapper.text()).toContain("0:00:12");
    expect(wrapper.text()).toContain("exec");
    expect(wrapper.text()).toContain("最近错误 / 告警");
    expect(wrapper.text()).toContain("快捷操作");
  });

  it("renders channel status cards with qq, weixin, feishu and email details", async () => {
    mockDashboardFetch(
      [{ date: "2026-03-06", model: "Gemini3Pro", total_tokens: 100 }],
      [{ date: "2026-03-06", p50_ms: 50, p95_ms: 80, p99_ms: 120 }],
    );

    const wrapper = mount(DashboardView, {
      props: {
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
    });

    await flushUi();
    await flushUi();
    await flushUi();

    expect(wrapper.text()).toContain("系统状态");
    expect(wrapper.text()).toContain("QQ");
    expect(wrapper.text()).toContain("QQ Bot");
    expect(wrapper.text()).toContain("••••4756");
    expect(wrapper.text()).toContain("WS 已连接");
    expect(wrapper.text()).toContain("收 3 / 发 2");
    expect(wrapper.text()).toContain("微信");
    expect(wrapper.text()).toContain("wx-bot-1");
    expect(wrapper.text()).toContain("收 12 / 发 9");
    expect(wrapper.text()).toContain("飞书");
    expect(wrapper.text()).toContain("••••ishu");
    expect(wrapper.text()).toContain("活跃会话 2");
    expect(wrapper.text()).toContain("hyx021203@shanghaitech.edu.cn");
    expect(wrapper.text()).toContain("active tasks");
  });

  it("renders WebUI, QQ, 微信 and 飞书 channel cards in the DOM", async () => {
    mockDashboardFetch(
      [{ date: "2026-03-06", model: "Gemini3Pro", total_tokens: 100 }],
      [{ date: "2026-03-06", p50_ms: 50, p95_ms: 80, p99_ms: 120 }],
    );

    const wrapper = mount(DashboardView, {
      props: {
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
    });

    await flushUi();
    await flushUi();
    await flushUi();

    // All three channel types are rendered as named cards in the DOM
    expect(wrapper.text()).toContain("WebUI");
    expect(wrapper.text()).toContain("QQ Bot");
    expect(wrapper.text()).toContain("微信");
    expect(wrapper.text()).toContain("飞书");
  });

  it("builds token chart with date xAxis and model-based series", async () => {
    mockDashboardFetch(
      [
        { date: "2026-03-06T00:12:00+00:00", model: "model-b", total_tokens: 12 },
        { date: "2026-03-05T09:00:00+00:00", model: "model-a", total_tokens: 5 },
        { date: "2026-03-06T11:00:00+00:00", model: "model-a", total_tokens: 9 },
        { date: "2026-03-06T12:00:00+00:00", model: "model-c", total_tokens: 4 },
      ],
      [{ date: "2026-03-06T00:00:00+00:00", p50_ms: 50, p95_ms: 80, p99_ms: 120 }],
    );

    const wrapper = mount(DashboardView, {
      props: {
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
    });
    await flushUi();
    await flushUi();
    await flushUi();

    const option = (
      wrapper.vm as unknown as { tokenChartOption: Record<string, unknown> }
    ).tokenChartOption;
    const xAxis = option.xAxis as { data: string[] };
    const legend = option.legend as { data: string[]; top?: string | number };
    const series = option.series as Array<{ name: string; data: number[] }>;
    const seriesByName = Object.fromEntries(series.map((item) => [item.name, item.data]));

    expect(xAxis.data).toEqual(["2026-03-05", "2026-03-06"]);
    expect(xAxis.data.every((item) => /^\d{4}-\d{2}-\d{2}$/.test(item))).toBe(true);
    expect([...legend.data].sort()).toEqual(["model-a", "model-b", "model-c"]);
    expect(legend.top === "top" || legend.top === 0).toBe(true);
    expect(series).toHaveLength(3);
    expect(seriesByName["model-a"]).toEqual([5, 9]);
    expect(seriesByName["model-b"]).toEqual([0, 12]);
    expect(seriesByName["model-c"]).toEqual([0, 4]);
  });

  it("renders the latency section as two full-width cards with card-owned headings", async () => {
    const tokenRows = [
      { date: "2026-03-06", model: "model-a", total_tokens: 100 },
      { date: "2026-03-06", model: "model-b", total_tokens: 80 },
      { date: "2026-03-06", model: "model-c", total_tokens: 60 },
    ];
    mockDashboardFetch(
      tokenRows,
      [
        { date: "2026-03-06T10:20:30+00:00", p50_ms: 60, p95_ms: 120, p99_ms: 200 },
        { date: "2026-03-05T08:10:00+00:00", p50_ms: 50, p95_ms: 90, p99_ms: 150 },
      ],
    );

    const wrapper = mount(DashboardView, {
      props: {
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
    });
    await flushUi();
    await flushUi();
    await flushUi();

    expect(wrapper.text()).toContain("模型响应延迟统计");
    expect(wrapper.text()).toContain("各模型调用耗时分布（ms）");
    expect(wrapper.text()).toContain("最近调用延迟");
    expect(wrapper.text()).toContain("最近调用的实际响应时间（ms）");
    expect(wrapper.text()).not.toContain("Latency P50/P95/P99");
  });

  it("builds latency comparison chart with model xAxis and percentile bar series", async () => {
    mockDashboardFetch(
      [{ date: "2026-03-06", model: "Gemini3Pro", total_tokens: 100 }],
      [
        { model: "Claude-3.5", p50_ms: 80, p95_ms: 150, p99_ms: 260 },
        { model: "GPT-4o", p50_ms: 60, p95_ms: 110, p99_ms: 180 },
      ],
    );

    const wrapper = mount(DashboardView, {
      props: {
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
    });
    await flushUi();
    await flushUi();
    await flushUi();

    const option = (
      wrapper.vm as unknown as { latencyDistributionOption: Record<string, unknown> }
    ).latencyDistributionOption;
    const xAxis = option.xAxis as { data: string[] };
    const legend = option.legend as { data: string[] };
    const series = option.series as Array<{ name: string; type: string; data: number[] }>;
    const seriesByName = Object.fromEntries(series.map((item) => [item.name, item.data]));

    expect(xAxis.data).toEqual(["Claude-3.5", "GPT-4o"]);
    expect(legend.data).toEqual(["P50", "P95", "P99"]);
    expect(option.title).toBeUndefined();
    expect(series.map((item) => item.type)).toEqual(["bar", "bar", "bar"]);
    expect(seriesByName.P50).toEqual([80, 60]);
    expect(seriesByName.P95).toEqual([150, 110]);
    expect(seriesByName.P99).toEqual([260, 180]);
  });

  it("builds recent latency line chart with per-model series and formatted timestamps", async () => {
    mockDashboardFetch(
      [{ date: "2026-03-06", model: "Gemini3Pro", total_tokens: 100 }],
      [{ model: "Gemini3Pro", p50_ms: 50, p95_ms: 80, p99_ms: 120 }],
      [
        { model: "Gemini3Pro", latency_ms: 88, timestamp: "2026-03-06T10:20:30+00:00" },
        { model: "Claude-3.5", latency_ms: 132, timestamp: "2026-03-06T10:21:00+00:00" },
        { model: "Gemini3Pro", latency_ms: 91, timestamp: "2026-03-06T10:21:30+00:00" },
      ],
    );

    const wrapper = mount(DashboardView, {
      props: {
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
    });
    await flushUi();
    await flushUi();
    await flushUi();

    const option = (
      wrapper.vm as unknown as { recentLatencyOption: Record<string, unknown> }
    ).recentLatencyOption;
    const xAxis = option.xAxis as { data: string[] };
    const legend = option.legend as { data: string[] };
    const series = option.series as Array<{ name: string; type: string; data: Array<number | null> }>;
    const seriesByName = Object.fromEntries(series.map((item) => [item.name, item.data]));

    expect(xAxis.data).toHaveLength(3);
    expect(legend.data).toEqual(["Claude-3.5", "Gemini3Pro"]);
    expect(series.map((item) => item.type)).toEqual(["line", "line"]);
    expect(seriesByName["Claude-3.5"]).toEqual([null, 132, null]);
    expect(seriesByName.Gemini3Pro).toEqual([88, null, 91]);
  });

  it("shows backend-api placeholder when recent latency endpoint is unavailable", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/dashboard/recent-latency?")) {
        return {
          ok: false,
          status: 404,
          json: async () => ({ detail: "not found" }),
        };
      }
      if (url.includes("/dashboard/status?")) {
        return {
          ok: true,
          json: async () => ({
            uptime_seconds: 12,
            uptime_human: "0:00:12",
            session_count: 3,
            kill_switch: false,
            bwrap_available: true,
          }),
        };
      }
      if (url.includes("/dashboard/token-stats?")) {
        return {
          ok: true,
          json: async () => ({ data: [{ date: "2026-03-06", model: "Gemini3Pro", total_tokens: 100 }] }),
        };
      }
      if (url.includes("/dashboard/latency-stats?") && url.includes("group_by=model")) {
        return {
          ok: true,
          json: async () => ({ data: [{ model: "Gemini3Pro", p50_ms: 50, p95_ms: 80, p99_ms: 120 }] }),
        };
      }
      if (url.includes("/dashboard/recent-tasks?")) {
        return {
          ok: true,
          json: async () => ({ data: [] }),
        };
      }
      if (url.includes("/dashboard/skills?")) {
        return {
          ok: true,
          json: async () => ({ data: [] }),
        };
      }
      if (url.includes("/channels/status?")) {
        return {
          ok: true,
          json: async () => ({
            channels: {
              webui: { status: "connected", active_connections: 1, last_message_at: new Date().toISOString() },
              qq_bot: {
                status: "connected",
                qq_bot_enabled: true,
                qq_bot_app_id: "••••4756",
                ws_connected: true,
                connected_at: null,
                last_message_at: new Date().toISOString(),
                messages_received: 3,
                messages_sent: 2,
              },
              weixin: {
                status: "connected",
                bot_id: "wx-bot-1",
                user_id: "target@im.wechat",
                last_message_at: new Date().toISOString(),
                messages_received: 12,
                messages_sent: 9,
              },
              email: {
                status: "enabled",
                accounts: ["hyx021203@shanghaitech.edu.cn"],
                last_scan_at: new Date().toISOString(),
                next_scan_at: new Date().toISOString(),
                emails_processed: 15,
              },
              heartbeat: {
                status: "running",
                last_heartbeat_at: new Date().toISOString(),
                active_tasks: 2,
              },
            },
          }),
        };
      }
      if (url.includes("/sessions?")) {
        return {
          ok: true,
          json: async () => [],
        };
      }
      return {
        ok: true,
        json: async () => ({}),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(DashboardView, {
      props: {
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
    });
    await flushUi();
    await flushUi();
    await flushUi();

    expect(wrapper.text()).toContain("需要后端提供原始调用记录 API");
  });

  it("applies dark chart theme and readable text colors in dark mode", async () => {
    document.documentElement.dataset.theme = "dark";
    mockDashboardFetch(
      [{ date: "2026-03-06", model: "Gemini3Pro", total_tokens: 100 }],
      [{ model: "Gemini3Pro", p50_ms: 50, p95_ms: 80, p99_ms: 120 }],
      [{ model: "Gemini3Pro", latency_ms: 88, timestamp: "2026-03-06T10:20:30+00:00" }],
    );

    const wrapper = mount(DashboardView, {
      props: {
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
    });
    await flushUi();
    await flushUi();
    await flushUi();

    const vm = wrapper.vm as unknown as {
      tokenChartOption: Record<string, unknown>;
      latencyDistributionOption: Record<string, unknown>;
      recentLatencyOption: Record<string, unknown>;
    };
    const tokenOption = vm.tokenChartOption;
    const latencyDistributionOption = vm.latencyDistributionOption;
    const recentLatencyOption = vm.recentLatencyOption;
    const chartNodes = wrapper.findAll(".chart-stub");

    expect(chartNodes[0]?.attributes("data-theme")).toBe("dark");
    expect(chartNodes[1]?.attributes("data-theme")).toBe("dark");
    expect(chartNodes[2]?.attributes("data-theme")).toBe("dark");
    expect((tokenOption.textStyle as { color: string }).color).toBe("#c6d1e6");
    expect((latencyDistributionOption.textStyle as { color: string }).color).toBe("#c6d1e6");
    expect((recentLatencyOption.textStyle as { color: string }).color).toBe("#c6d1e6");
  });
});
