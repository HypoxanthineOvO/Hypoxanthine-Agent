<script setup lang="ts">
import { NButton, NCard, NDataTable, NEmpty, NGrid, NGridItem, NTag } from "naive-ui";
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

const emit = defineEmits<{
  'open-session': [sessionId: string]
}>();

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

interface RecentSessionRow {
  session_id: string;
  message_count: number;
  updated_at: string;
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

interface WebUiChannelStatus {
  status: string;
  active_connections: number;
  last_message_at: string | null;
}

interface QQChannelStatus {
  status: string;
  bot_qq: string;
  napcat_ws_url: string;
  connected_at: string | null;
  last_message_at: string | null;
  messages_received: number;
  messages_sent: number;
}

interface EmailChannelStatus {
  status: string;
  accounts: string[];
  last_scan_at: string | null;
  next_scan_at: string | null;
  emails_processed: number;
}

interface HeartbeatChannelStatus {
  status: string;
  last_heartbeat_at: string | null;
  active_tasks: number;
}

interface ChannelsStatusResponse {
  channels: {
    webui: WebUiChannelStatus;
    qq: QQChannelStatus;
    email: EmailChannelStatus;
    heartbeat: HeartbeatChannelStatus;
  };
}

interface ChannelCard {
  key: string;
  icon: string;
  name: string;
  status: string;
  statusLabel: string;
  tagType: "success" | "warning" | "error" | "default";
  details: string[];
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
const recentSessions = ref<RecentSessionRow[]>([]);
const skills = ref<SkillRow[]>([]);
const channels = ref<ChannelsStatusResponse["channels"] | null>(null);
let refreshTimer: ReturnType<typeof setInterval> | null = null;
let channelRefreshTimer: ReturnType<typeof setInterval> | null = null;
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

const formatRelativeTime = (raw: string | null | undefined): string => {
  if (!raw) {
    return "暂无";
  }
  const timestamp = new Date(raw).getTime();
  if (Number.isNaN(timestamp)) {
    return raw;
  }
  const diff = timestamp - Date.now();
  const absDiff = Math.abs(diff);
  if (absDiff < 60_000) {
    return "刚刚";
  }
  const minutes = Math.round(absDiff / 60_000);
  if (minutes < 60) {
    return diff >= 0 ? `${minutes} 分钟后` : `${minutes} 分钟前`;
  }
  const hours = Math.round(absDiff / 3_600_000);
  if (hours < 24) {
    return diff >= 0 ? `${hours} 小时后` : `${hours} 小时前`;
  }
  const days = Math.round(absDiff / 86_400_000);
  return diff >= 0 ? `${days} 天后` : `${days} 天前`;
};

const channelTagType = (
  status: string,
): "success" | "warning" | "error" | "default" => {
  if (["connected", "running", "enabled"].includes(status)) {
    return "success";
  }
  if (["connecting", "scanning"].includes(status)) {
    return "warning";
  }
  if (["disconnected", "error", "open"].includes(status)) {
    return "error";
  }
  return "default";
};

const channelStatusLabel = (status: string): string => {
  switch (status) {
    case "connected":
      return "🟢 已连接";
    case "running":
      return "🟢 运行中";
    case "enabled":
      return "🟢 已启用";
    case "connecting":
      return "🟡 连接中";
    case "scanning":
      return "🟡 扫描中";
    case "disconnected":
      return "🔴 未连接";
    case "error":
      return "🔴 异常";
    case "disabled":
      return "⚪ 已禁用";
    default:
      return status || "未知";
  }
};

const channelCards = computed<ChannelCard[]>(() => {
  const payload = channels.value;
  if (!payload) {
    return [];
  }
  return [
    {
      key: "webui",
      icon: "🖥️",
      name: "WebUI",
      status: payload.webui.status,
      statusLabel: channelStatusLabel(payload.webui.status),
      tagType: channelTagType(payload.webui.status),
      details: [
        `活跃连接 ${payload.webui.active_connections}`,
        `最后消息 ${formatRelativeTime(payload.webui.last_message_at)}`,
      ],
    },
    {
      key: "qq",
      icon: "🐧",
      name: "QQ",
      status: payload.qq.status,
      statusLabel: channelStatusLabel(payload.qq.status),
      tagType: channelTagType(payload.qq.status),
      details: [
        `Bot QQ ${payload.qq.bot_qq || "未配置"}`,
        payload.qq.napcat_ws_url || "未配置 WS URL",
        `收 ${payload.qq.messages_received} / 发 ${payload.qq.messages_sent}`,
        `最后消息 ${formatRelativeTime(payload.qq.last_message_at)}`,
      ],
    },
    {
      key: "email",
      icon: "📧",
      name: "邮箱",
      status: payload.email.status,
      statusLabel: channelStatusLabel(payload.email.status),
      tagType: channelTagType(payload.email.status),
      details: [
        payload.email.accounts.join(", ") || "未配置邮箱账号",
        `上次扫描 ${formatRelativeTime(payload.email.last_scan_at)}`,
        `下次扫描 ${formatRelativeTime(payload.email.next_scan_at)}`,
        `累计处理 ${payload.email.emails_processed} 封`,
      ],
    },
    {
      key: "heartbeat",
      icon: "💓",
      name: "心跳",
      status: payload.heartbeat.status,
      statusLabel: channelStatusLabel(payload.heartbeat.status),
      tagType: channelTagType(payload.heartbeat.status),
      details: [
        `最后心跳 ${formatRelativeTime(payload.heartbeat.last_heartbeat_at)}`,
        `active tasks ${payload.heartbeat.active_tasks}`,
      ],
    },
  ];
});

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
      type: "scroll",
      top: "top",
      left: "center",
      textStyle: { color: chartTextColor.value },
    },
    grid: { left: 16, right: 16, top: 48, bottom: 20, containLabel: true },
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
    title: {
      text: "模型响应延迟",
      left: "center",
      top: 4,
      textStyle: { color: chartTextColor.value, fontSize: 13 },
    },
    tooltip: { trigger: "axis" },
    legend: {
      data: ["P50", "P95", "P99"],
      top: "top",
      left: "center",
      textStyle: { color: chartTextColor.value },
    },
    grid: { left: 16, right: 16, top: 52, bottom: 20, containLabel: true },
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

const loadChannels = async (): Promise<void> => {
  const response = await apiGetJson<ChannelsStatusResponse>(withToken("channels/status"));
  channels.value = response.channels;
};

const loadRecentSessions = async (): Promise<void> => {
  try {
    const data = await apiGetJson<RecentSessionRow[]>(withToken("sessions"));
    recentSessions.value = [...data]
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
      .slice(0, 8);
  } catch {
    recentSessions.value = [];
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
  void loadChannels();
  void loadRecentSessions();
  refreshTimer = setInterval(() => {
    void loadDashboard();
    void loadRecentSessions();
  }, 5000);
  channelRefreshTimer = setInterval(() => {
    void loadChannels();
  }, 30000);
});

onUnmounted(() => {
  if (themeObserver) {
    themeObserver.disconnect();
  }
  if (refreshTimer !== null) {
    clearInterval(refreshTimer);
  }
  if (channelRefreshTimer !== null) {
    clearInterval(channelRefreshTimer);
  }
});
</script>

<template>
  <section class="dashboard-view">
    <n-grid :x-gap="12" :y-gap="12" cols="1 s:1 m:2 l:2" responsive="screen">
      <n-grid-item span="2 m:2 l:2">
        <n-card :bordered="false">
          <template #header>
            <div class="card-header-row">
              <span>渠道状态</span>
              <n-button size="small" tertiary @click="void loadChannels()">刷新渠道状态</n-button>
            </div>
          </template>
          <div class="channel-grid">
            <n-card
              v-for="channel in channelCards"
              :key="channel.key"
              size="small"
              embedded
              class="channel-card"
            >
              <div class="channel-card-header">
                <div class="channel-title-wrap">
                  <span class="channel-icon">{{ channel.icon }}</span>
                  <div>
                    <p class="channel-name">{{ channel.name }}</p>
                    <p class="channel-status-text">{{ channel.statusLabel }}</p>
                  </div>
                </div>
                <n-tag :type="channel.tagType">{{ channel.statusLabel }}</n-tag>
              </div>
              <ul class="channel-details">
                <li v-for="detail in channel.details" :key="detail">{{ detail }}</li>
              </ul>
            </n-card>
          </div>
        </n-card>
      </n-grid-item>

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
          <div v-if="latencyStats.length === 0" style="height: 280px; display: flex; align-items: center; justify-content: center">
            <n-empty description="暂无延迟数据" />
          </div>
          <v-chart v-else autoresize :option="latencyChartOption" :theme="chartTheme" class="chart" />
        </n-card>
      </n-grid-item>

      <n-grid-item span="2 m:2 l:2">
        <n-card title="最近对话" :bordered="false">
          <div v-if="recentSessions.length === 0" class="empty-sessions">
            <n-empty description="暂无对话记录" />
          </div>
          <ul v-else class="session-list">
            <li
              v-for="session in recentSessions"
              :key="session.session_id"
              class="session-item"
              @click="emit('open-session', session.session_id)"
            >
              <div class="session-info">
                <span class="session-id">{{ session.session_id.slice(0, 50) }}</span>
                <span class="session-time">{{ formatRelativeTime(session.updated_at) }}</span>
              </div>
              <span class="session-count">{{ session.message_count }} 条</span>
            </li>
          </ul>
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

.card-header-row {
  align-items: center;
  display: flex;
  justify-content: space-between;
  width: 100%;
}

.channel-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.channel-card {
  min-height: 168px;
}

.channel-card-header {
  align-items: flex-start;
  display: flex;
  gap: 12px;
  justify-content: space-between;
}

.channel-title-wrap {
  align-items: flex-start;
  display: flex;
  gap: 10px;
}

.channel-icon {
  font-size: 20px;
  line-height: 1;
}

.channel-name {
  font-size: 15px;
  font-weight: 700;
  margin: 0;
}

.channel-status-text {
  color: var(--muted);
  font-size: 12px;
  margin: 4px 0 0;
}

.channel-details {
  color: var(--muted);
  display: grid;
  gap: 8px;
  list-style: none;
  margin: 14px 0 0;
  padding: 0;
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

.empty-sessions {
  align-items: center;
  display: flex;
  height: 200px;
  justify-content: center;
}

.session-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  list-style: none;
  margin: 0;
  padding: 0;
}

.session-item {
  align-items: center;
  background: color-mix(in srgb, var(--surface) 88%, transparent);
  border: 1px solid var(--panel-edge);
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  padding: 12px 16px;
  transition: all 0.2s ease;
}

.session-item:hover {
  background: var(--surface-hover);
  border-color: var(--primary-color);
  transform: translateY(-1px);
}

.session-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.session-id {
  color: var(--text-color);
  font-family: monospace;
  font-size: 14px;
  font-weight: 500;
}

.session-time {
  color: var(--muted);
  font-size: 12px;
}

.session-count {
  background: var(--surface-active);
  border-radius: 12px;
  color: var(--text-color);
  font-size: 13px;
  padding: 4px 12px;
}

@media (max-width: 767px) {
  .status-grid {
    grid-template-columns: 1fr;
  }

  .channel-grid {
    grid-template-columns: 1fr;
  }
}
</style>
