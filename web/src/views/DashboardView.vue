<script setup lang="ts">
import { NCard, NDataTable, NGrid, NGridItem, NTag } from "naive-ui";
import { computed, onMounted, onUnmounted, ref } from "vue";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from "echarts/components";

import { apiGetJson } from "../utils/apiClient";

use([
  CanvasRenderer,
  LineChart,
  BarChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
]);

const props = withDefaults(
  defineProps<{
    token: string;
    apiBase?: string;
  }>(),
  {
    apiBase: "",
  },
);

interface DashboardStatus {
  uptime_seconds: number;
  uptime_human: string;
  session_count: number;
  kill_switch: boolean;
  bwrap_available: boolean;
}

interface TokenStatsRow {
  date: string;
  model: string;
  total_tokens: number;
}

interface LatencyStatsRow {
  date: string;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
}

interface RecentTaskRow {
  id: number;
  created_at: string;
  tool_name: string;
  status: string;
  duration_ms: number | null;
}

interface SkillRow {
  name: string;
  status: "healthy" | "open" | "disabled";
  tools: string[];
}

const normalizedApiBase = computed(() => {
  const explicitBase = props.apiBase.trim();
  if (explicitBase) {
    return explicitBase.replace(/\/+$/, "");
  }
  return "/api";
});

const withToken = (path: string): string => {
  const base = `${normalizedApiBase.value}/${path.replace(/^\/+/, "")}`;
  const separator = base.includes("?") ? "&" : "?";
  return `${base}${separator}token=${encodeURIComponent(props.token)}`;
};

const loading = ref(false);
const status = ref<DashboardStatus | null>(null);
const tokenStats = ref<TokenStatsRow[]>([]);
const latencyStats = ref<LatencyStatsRow[]>([]);
const recentTasks = ref<RecentTaskRow[]>([]);
const skills = ref<SkillRow[]>([]);
let refreshTimer: ReturnType<typeof setInterval> | null = null;
let themeObserver: MutationObserver | null = null;

const themeMode = ref<"light" | "dark">(
  document.documentElement.dataset.theme === "dark" ? "dark" : "light",
);

const syncThemeMode = (): void => {
  themeMode.value = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
};

const isDarkMode = computed(() => themeMode.value === "dark");
const chartTheme = computed(() => (isDarkMode.value ? "dark" : undefined));
const chartTextColor = computed(() => (isDarkMode.value ? "#e0e0e0" : "#4b5563"));
const chartAxisColor = computed(() => (isDarkMode.value ? "#64748b" : "#94a3b8"));
const chartSplitLineColor = computed(() =>
  isDarkMode.value ? "rgba(148, 163, 184, 0.22)" : "rgba(148, 163, 184, 0.35)",
);

const normalizeDateKey = (raw: string): string => {
  const value = String(raw ?? "").trim();
  const match = value.match(/^(\d{4}-\d{2}-\d{2})/);
  return match?.[1] ?? value;
};

const tokenChartOption = computed(() => {
  const dates = [...new Set(tokenStats.value.map((item) => normalizeDateKey(item.date)))].sort((a, b) =>
    a.localeCompare(b),
  );
  const models = [...new Set(tokenStats.value.map((item) => item.model))].sort((a, b) =>
    a.localeCompare(b),
  );
  const totalsByModelDate = new Map<string, number>();
  for (const item of tokenStats.value) {
    const dateKey = normalizeDateKey(item.date);
    const key = `${item.model}::${dateKey}`;
    const current = totalsByModelDate.get(key) ?? 0;
    totalsByModelDate.set(key, current + Number(item.total_tokens ?? 0));
  }
  const series = models.map((model) => {
    const values = dates.map((date) => {
      return totalsByModelDate.get(`${model}::${date}`) ?? 0;
    });
    return {
      name: model,
      type: "bar",
      data: values,
      stack: "total",
    };
  });
  return {
    backgroundColor: "transparent",
    textStyle: { color: chartTextColor.value },
    tooltip: { trigger: "axis" },
    legend: {
      data: models,
      top: "top",
      left: "center",
      textStyle: { color: chartTextColor.value },
    },
    grid: { left: 16, right: 16, top: 32, bottom: 20, containLabel: true },
    xAxis: {
      type: "category",
      data: dates,
      axisLabel: { color: chartTextColor.value },
      axisLine: { lineStyle: { color: chartAxisColor.value } },
    },
    yAxis: {
      type: "value",
      name: "Tokens",
      axisLabel: { color: chartTextColor.value },
      axisLine: { lineStyle: { color: chartAxisColor.value } },
      splitLine: { lineStyle: { color: chartSplitLineColor.value } },
    },
    series,
  };
});

const latencyChartOption = computed(() => {
  const normalizedRows = latencyStats.value.map((item) => ({
    ...item,
    date: normalizeDateKey(item.date),
  }));
  const dates = [...new Set(normalizedRows.map((item) => item.date))].sort((a, b) =>
    a.localeCompare(b),
  );
  const rowByDate = new Map<string, LatencyStatsRow>();
  for (const row of normalizedRows) {
    rowByDate.set(row.date, row);
  }
  return {
    backgroundColor: "transparent",
    textStyle: { color: chartTextColor.value },
    tooltip: { trigger: "axis" },
    legend: {
      data: ["P50", "P95", "P99"],
      top: "top",
      left: "center",
      textStyle: { color: chartTextColor.value },
    },
    grid: { left: 16, right: 16, top: 32, bottom: 20, containLabel: true },
    xAxis: {
      type: "category",
      data: dates,
      axisLabel: { color: chartTextColor.value },
      axisLine: { lineStyle: { color: chartAxisColor.value } },
    },
    yAxis: {
      type: "value",
      name: "ms",
      axisLabel: { color: chartTextColor.value },
      axisLine: { lineStyle: { color: chartAxisColor.value } },
      splitLine: { lineStyle: { color: chartSplitLineColor.value } },
    },
    series: [
      {
        name: "P50",
        type: "line",
        smooth: true,
        data: dates.map((date) => rowByDate.get(date)?.p50_ms ?? 0),
      },
      {
        name: "P95",
        type: "line",
        smooth: true,
        data: dates.map((date) => rowByDate.get(date)?.p95_ms ?? 0),
      },
      {
        name: "P99",
        type: "line",
        smooth: true,
        data: dates.map((date) => rowByDate.get(date)?.p99_ms ?? 0),
      },
    ],
  };
});

const taskColumns = [
  {
    title: "时间",
    key: "created_at",
  },
  {
    title: "工具",
    key: "tool_name",
  },
  {
    title: "状态",
    key: "status",
  },
  {
    title: "耗时(ms)",
    key: "duration_ms",
  },
];

const loadDashboard = async (): Promise<void> => {
  loading.value = true;
  try {
    const [statusResp, tokenResp, latencyResp, tasksResp, skillsResp] = await Promise.all([
      apiGetJson<DashboardStatus>(withToken("dashboard/status")),
      apiGetJson<{ data: TokenStatsRow[] }>(withToken("dashboard/token-stats?days=7")),
      apiGetJson<{ data: LatencyStatsRow[] }>(withToken("dashboard/latency-stats?days=7")),
      apiGetJson<{ data: RecentTaskRow[] }>(withToken("dashboard/recent-tasks?limit=20")),
      apiGetJson<{ data: SkillRow[] }>(withToken("dashboard/skills")),
    ]);
    status.value = statusResp;
    tokenStats.value = tokenResp.data ?? [];
    latencyStats.value = latencyResp.data ?? [];
    recentTasks.value = tasksResp.data ?? [];
    skills.value = skillsResp.data ?? [];
  } finally {
    loading.value = false;
  }
};

onMounted(() => {
  syncThemeMode();
  themeObserver = new MutationObserver(() => {
    syncThemeMode();
  });
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  void loadDashboard();
  refreshTimer = setInterval(() => {
    void loadDashboard();
  }, 5000);
});

onUnmounted(() => {
  if (themeObserver) {
    themeObserver.disconnect();
  }
  if (refreshTimer !== null) {
    clearInterval(refreshTimer);
  }
});
</script>

<template>
  <section class="dashboard-view">
    <n-grid :x-gap="12" :y-gap="12" cols="1 s:1 m:2 l:2" responsive="screen">
      <n-grid-item>
        <n-card title="系统状态" :bordered="false">
          <div v-if="status" class="status-grid">
            <div class="status-item">
              <p class="status-label">Uptime</p>
              <p class="status-value">{{ status.uptime_human }}</p>
            </div>
            <div class="status-item">
              <p class="status-label">Sessions</p>
              <p class="status-value">{{ status.session_count }}</p>
            </div>
            <div class="status-item">
              <p class="status-label">Kill Switch</p>
              <p class="status-value">{{ status.kill_switch ? "ON" : "OFF" }}</p>
            </div>
            <div class="status-item">
              <p class="status-label">bwrap</p>
              <p class="status-value">{{ status.bwrap_available ? "Available" : "Missing" }}</p>
            </div>
          </div>
        </n-card>
      </n-grid-item>

      <n-grid-item>
        <n-card title="Skills 状态" :bordered="false">
          <ul class="skills-list">
            <li
              v-for="skill in skills"
              :key="skill.name"
              class="skill-row"
            >
              <span>{{ skill.name }}</span>
              <n-tag :type="skill.status === 'healthy' ? 'success' : skill.status === 'open' ? 'error' : 'warning'">
                {{ skill.status === "healthy" ? "🟢 正常" : skill.status === "open" ? "🔴 熔断" : "⏳ 禁用" }}
              </n-tag>
            </li>
          </ul>
        </n-card>
      </n-grid-item>

      <n-grid-item>
        <n-card title="Token 消耗（按模型）" :bordered="false">
          <v-chart autoresize :option="tokenChartOption" :theme="chartTheme" class="chart" />
        </n-card>
      </n-grid-item>

      <n-grid-item>
        <n-card title="Latency P50/P95/P99" :bordered="false">
          <v-chart autoresize :option="latencyChartOption" :theme="chartTheme" class="chart" />
        </n-card>
      </n-grid-item>

      <n-grid-item span="2 m:2 l:2">
        <n-card title="最近任务" :bordered="false">
          <n-data-table
            :columns="taskColumns"
            :data="recentTasks"
            :loading="loading"
            :pagination="false"
            size="small"
          />
        </n-card>
      </n-grid-item>
    </n-grid>
  </section>
</template>

<style scoped>
.dashboard-view {
  height: 100%;
  min-height: 0;
  overflow: auto;
}

.status-grid {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.status-item {
  background: color-mix(in srgb, var(--surface) 88%, transparent);
  border: 1px solid var(--panel-edge);
  border-radius: 12px;
  padding: 10px;
}

.status-label {
  color: var(--muted);
  font-size: 12px;
  margin: 0;
}

.status-value {
  font-size: 16px;
  font-weight: 700;
  margin: 2px 0 0;
}

.skills-list {
  display: grid;
  gap: 8px;
  list-style: none;
  margin: 0;
  padding: 0;
}

.skill-row {
  align-items: center;
  display: flex;
  justify-content: space-between;
}

.chart {
  height: 280px;
  width: 100%;
}

@media (max-width: 767px) {
  .status-grid {
    grid-template-columns: 1fr;
  }
}
</style>
