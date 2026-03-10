import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
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

import DashboardView from "../DashboardView.vue";

async function flushUi(): Promise<void> {
  await Promise.resolve();
  await nextTick();
}

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
    latencyRows: Array<Record<string, unknown>>,
  ): ReturnType<typeof vi.fn> => {
    const fetchMock = vi.fn();
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          uptime_seconds: 12,
          uptime_human: "0:00:12",
          session_count: 3,
          kill_switch: false,
          bwrap_available: true,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ data: tokenRows }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ data: latencyRows }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          data: [{ id: 1, created_at: "2026-03-06", tool_name: "run", status: "success", duration_ms: 10 }],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ data: [{ name: "tmux", status: "healthy", tools: ["run_command"] }] }),
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
    expect(wrapper.text()).toContain("0:00:12");
    expect(wrapper.text()).toContain("tmux");
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

  it("builds latency chart with date xAxis and P50/P95/P99 series", async () => {
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

    const option = (
      wrapper.vm as unknown as { latencyChartOption: Record<string, unknown> }
    ).latencyChartOption;
    const xAxis = option.xAxis as { data: string[] };
    const legend = option.legend as { data: string[]; top?: string | number };
    const series = option.series as Array<{ name: string; data: number[] }>;
    const seriesByName = Object.fromEntries(series.map((item) => [item.name, item.data]));
    const uniqueModelCount = new Set(tokenRows.map((item) => item.model)).size;

    expect(xAxis.data).toEqual(["2026-03-05", "2026-03-06"]);
    expect(xAxis.data.every((item) => /^\d{4}-\d{2}-\d{2}$/.test(item))).toBe(true);
    expect(legend.data).toEqual(["P50", "P95", "P99"]);
    expect(legend.top === "top" || legend.top === 0).toBe(true);
    expect(series).toHaveLength(3);
    expect(series).toHaveLength(uniqueModelCount);
    expect(seriesByName.P50).toEqual([50, 60]);
    expect(seriesByName.P95).toEqual([90, 120]);
    expect(seriesByName.P99).toEqual([150, 200]);
  });

  it("applies dark chart theme and readable text colors in dark mode", async () => {
    document.documentElement.dataset.theme = "dark";
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

    const vm = wrapper.vm as unknown as {
      tokenChartOption: Record<string, unknown>;
      latencyChartOption: Record<string, unknown>;
    };
    const tokenOption = vm.tokenChartOption;
    const latencyOption = vm.latencyChartOption;
    const chartNodes = wrapper.findAll(".chart-stub");

    expect(chartNodes[0]?.attributes("data-theme")).toBe("dark");
    expect(chartNodes[1]?.attributes("data-theme")).toBe("dark");
    expect((tokenOption.textStyle as { color: string }).color).toBe("#e0e0e0");
    expect((latencyOption.textStyle as { color: string }).color).toBe("#e0e0e0");
  });
});
