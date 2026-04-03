<script setup lang="ts">
import {
  NCard,
  NDrawer,
  NDrawerContent,
  NEmpty,
  NGrid,
  NGridItem,
  NSkeleton,
  NTag,
  useMessage,
} from "naive-ui";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";

import { apiGetJson, ApiClientError } from "../utils/apiClient";
import ChannelStatusCard from "../components/dashboard/ChannelStatusCard.vue";
import { useThemeMode } from "../composables/useThemeMode";
import type {
  ChannelCard,
  ChannelCardMap,
  ChannelsStatusResponse,
  DashboardStatus,
  EmailChannelStatus,
  FeishuChannelStatus,
  HeartbeatChannelStatus,
  ModelLatencyStatsRow,
  QQBotChannelStatus,
  RecentIssueRow,
  RecentLatencyRow,
  RecentTaskRow,
  SkillRow,
  TokenStatsRow,
  WeixinChannelStatus,
} from "../types/dashboard";
import {
  formatFullTime,
  formatRelativeTime,
  formatShortTime,
} from "../utils/timeFormat";

use([
  CanvasRenderer,
  LineChart,
  BarChart,
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
  'navigate': [view: "chat" | "dashboard" | "config" | "memory", sessionId?: string]
}>();

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
const latencyModelStats = ref<ModelLatencyStatsRow[]>([]);
const recentLatency = ref<RecentLatencyRow[]>([]);
const recentLatencyApiAvailable = ref(true);
const recentTasks = ref<RecentTaskRow[]>([]);
const recentIssues = ref<RecentIssueRow[]>([]);
const skills = ref<SkillRow[]>([]);
const channels = ref<ChannelsStatusResponse["channels"] | null>(null);
let refreshTimer: ReturnType<typeof setInterval> | null = null;
let channelRefreshTimer: ReturnType<typeof setInterval> | null = null;
const statsCardRef = ref<HTMLElement | null>(null);
const issueFilter = ref<"all" | "error" | "warning">("all");
const selectedIssue = ref<RecentIssueRow | null>(null);
const actionMessage = (() => {
  try {
    return useMessage();
  } catch {
    return {
      info: (_text: string) => undefined,
      success: (_text: string) => undefined,
      warning: (_text: string) => undefined,
    };
  }
})();

const { isDark } = useThemeMode();
const isDarkMode = computed(
  () => isDark.value || document.documentElement.dataset.theme === "dark",
);
const chartTheme = computed(() => (isDarkMode.value ? "dark" : undefined));
const cssVar = (name: string, fallback: string): string =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
const chartTextColor = computed(() => cssVar("--text-soft", isDarkMode.value ? "#c6d1e6" : "#54627a"));
const chartAxisColor = computed(() => cssVar("--muted", isDarkMode.value ? "#9eacc7" : "#687892"));
const chartSplitLineColor = computed(() => cssVar("--chart-grid", "rgba(148, 163, 184, 0.3)"));
const chartTooltipBackground = computed(() => cssVar("--tooltip-bg", isDarkMode.value ? "rgba(24,29,40,0.96)" : "rgba(255,255,255,0.96)"));
const chartTooltipText = computed(() => cssVar("--tooltip-text", isDarkMode.value ? "#eef3ff" : "#162033"));
const latencyPalette = computed(() => [
  cssVar("--chart-secondary", "#18a058"),
  cssVar("--chart-primary", "#2080f0"),
  cssVar("--chart-tertiary", "#f0a020"),
  cssVar("--chart-quaternary", "#7c3aed"),
]);

const normalizeDateKey = (raw: string): string => {
  const value = String(raw ?? "").trim();
  const match = value.match(/^(\d{4}-\d{2}-\d{2})/);
  return match?.[1] ?? value;
};

const issueFilterOptions = [
  { label: "全部", value: "all" },
  { label: "仅错误", value: "error" },
  { label: "仅告警", value: "warning" },
] as const;

const issueLevelLabel = (level: RecentIssueRow["level"]): string =>
  level === "error" ? "错误" : "告警";

const issueLevelIcon = (level: RecentIssueRow["level"]): string =>
  level === "error" ? "🔴" : "🟡";

const formatIssueSummary = (message: string): string =>
  message.length <= 80 ? message : `${message.slice(0, 79).trimEnd()}…`;

const openIssueDetail = (item: RecentIssueRow): void => {
  selectedIssue.value = item;
};

const closeIssueDetail = (): void => {
  selectedIssue.value = null;
};

const channelTagType = (
  status: string,
): "success" | "warning" | "error" | "default" => {
  if (["connected", "running", "enabled"].includes(status)) {
    return "success";
  }
  if (["connecting", "scanning", "no_token"].includes(status)) {
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
    case "no_token":
      return "🟡 缺少 Token";
    default:
      return status || "未知";
  }
};

const createLoadingChannelCard = (key: string, icon: string, name: string): ChannelCard => ({
  key,
  icon,
  name,
  status: "",
  statusLabel: "加载中",
  tagType: "default",
  details: ["正在读取通道状态"],
});

const createChannelCard = (
  key: string,
  icon: string,
  name: string,
  status: string,
  details: string[],
): ChannelCard => ({
  key,
  icon,
  name,
  status,
  statusLabel: channelStatusLabel(status),
  tagType: channelTagType(status),
  details,
});

const defaultQQBotStatus = (): QQBotChannelStatus => ({
  status: "disabled",
  qq_bot_enabled: false,
  qq_bot_app_id: "",
  ws_connected: false,
  connected_at: null,
  last_message_at: null,
  messages_received: 0,
  messages_sent: 0,
});

const defaultWeixinStatus = (): WeixinChannelStatus => ({
  status: "disabled",
  bot_id: "",
  user_id: "",
  last_message_at: null,
  messages_received: 0,
  messages_sent: 0,
});

const defaultEmailStatus = (): EmailChannelStatus => ({
  status: "disabled",
  accounts: [],
  last_scan_at: null,
  next_scan_at: null,
  emails_processed: 0,
});

const defaultFeishuStatus = (): FeishuChannelStatus => ({
  status: "disabled",
  app_id: "",
  chat_count: 0,
  last_message_at: null,
  messages_received: 0,
  messages_sent: 0,
});

const defaultHeartbeatStatus = (): HeartbeatChannelStatus => ({
  status: "disabled",
  last_heartbeat_at: null,
  active_tasks: 0,
});

const channelCardMap = computed<ChannelCardMap>(() => {
  const payload = channels.value;
  if (!payload) {
    return {
      webui: createLoadingChannelCard("webui", "🖥️", "WebUI"),
      qqBot: createLoadingChannelCard("qq_bot", "🐧", "QQ Bot"),
      qqNapcat: null,
      weixin: createLoadingChannelCard("weixin", "💬", "微信"),
      feishu: createLoadingChannelCard("feishu", "🪽", "飞书"),
      email: createLoadingChannelCard("email", "📧", "邮箱"),
      heartbeat: createLoadingChannelCard("heartbeat", "💓", "心跳"),
    };
  }

  const qqBot = payload.qq_bot ?? defaultQQBotStatus();
  const weixin = payload.weixin ?? defaultWeixinStatus();
  const feishu = payload.feishu ?? defaultFeishuStatus();
  const email = payload.email ?? defaultEmailStatus();
  const heartbeat = payload.heartbeat ?? defaultHeartbeatStatus();

  return {
    webui: createChannelCard("webui", "🖥️", "WebUI", payload.webui.status, [
      `活跃连接 ${payload.webui.active_connections}`,
      `最后消息 ${formatRelativeTime(payload.webui.last_message_at)}`,
    ]),
    qqBot: createChannelCard(
      "qq_bot",
      "🐧",
      "QQ Bot",
      qqBot.status,
      [
        `App ID ${qqBot.qq_bot_app_id || "未配置"}`,
        `WS ${qqBot.ws_connected ? "已连接" : "未连接"}`,
        `收 ${qqBot.messages_received} / 发 ${qqBot.messages_sent}`,
        `最后消息 ${formatRelativeTime(qqBot.last_message_at)}`,
      ],
    ),
    qqNapcat: payload.qq_napcat
      ? createChannelCard("qq_napcat", "📡", "QQ NapCat", payload.qq_napcat.status, [
          `Bot QQ ${payload.qq_napcat.bot_qq || "未配置"}`,
          payload.qq_napcat.napcat_ws_url || "未配置 WS URL",
          `收 ${payload.qq_napcat.messages_received} / 发 ${payload.qq_napcat.messages_sent}`,
          `最后消息 ${formatRelativeTime(payload.qq_napcat.last_message_at)}`,
        ])
      : null,
    weixin: createChannelCard("weixin", "💬", "微信", weixin.status, [
      `Bot ID ${weixin.bot_id || "未登录"}`,
      `目标用户 ${weixin.user_id || "未绑定"}`,
      `收 ${weixin.messages_received} / 发 ${weixin.messages_sent}`,
      `最后消息 ${formatRelativeTime(weixin.last_message_at)}`,
    ]),
    feishu: createChannelCard("feishu", "🪽", "飞书", feishu.status, [
      `App ID ${feishu.app_id || "未配置"}`,
      `活跃会话 ${feishu.chat_count}`,
      `收 ${feishu.messages_received} / 发 ${feishu.messages_sent}`,
      `最后消息 ${formatRelativeTime(feishu.last_message_at)}`,
    ]),
    email: createChannelCard("email", "📧", "邮箱", email.status, [
      email.accounts.join(", ") || "未配置邮箱账号",
      `上次扫描 ${formatRelativeTime(email.last_scan_at)}`,
      `下次扫描 ${formatRelativeTime(email.next_scan_at)}`,
      `累计处理 ${email.emails_processed} 封`,
    ]),
    heartbeat: createChannelCard("heartbeat", "💓", "心跳", heartbeat.status, [
      `最后心跳 ${formatRelativeTime(heartbeat.last_heartbeat_at)}`,
      `active tasks ${heartbeat.active_tasks}`,
    ]),
  };
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
      itemStyle: {
        color: latencyPalette.value[models.indexOf(model) % latencyPalette.value.length],
      },
    };
  });
  return {
    backgroundColor: "transparent",
    textStyle: { color: chartTextColor.value },
    tooltip: {
      trigger: "axis",
      backgroundColor: chartTooltipBackground.value,
      borderColor: chartSplitLineColor.value,
      textStyle: { color: chartTooltipText.value },
    },
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

const latencyDistributionOption = computed(() => {
  const rows = latencyModelStats.value
    .filter((item) => typeof item?.model === "string" && item.model.trim().length > 0)
    .sort((left, right) => left.model.localeCompare(right.model));
  const models = rows.map((item) => item.model);
  return {
    backgroundColor: "transparent",
    textStyle: { color: chartTextColor.value },
    tooltip: {
      trigger: "axis",
      backgroundColor: chartTooltipBackground.value,
      borderColor: chartSplitLineColor.value,
      textStyle: { color: chartTooltipText.value },
      valueFormatter: (value: number | string) => `${Math.round(Number(value))} ms`,
    },
    legend: {
      data: ["P50", "P95", "P99"],
      top: "top",
      left: "center",
      textStyle: { color: chartTextColor.value },
    },
    grid: { left: 16, right: 16, top: 48, bottom: 20, containLabel: true },
    xAxis: {
      type: "category",
      data: models,
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
        type: "bar",
        barMaxWidth: 24,
        itemStyle: { color: latencyPalette.value[0] },
        data: rows.map((item) => item.p50_ms),
      },
      {
        name: "P95",
        type: "bar",
        barMaxWidth: 24,
        itemStyle: { color: latencyPalette.value[1] },
        data: rows.map((item) => item.p95_ms),
      },
      {
        name: "P99",
        type: "bar",
        barMaxWidth: 24,
        itemStyle: { color: latencyPalette.value[2] },
        data: rows.map((item) => item.p99_ms),
      },
    ],
  };
});

const recentLatencyOption = computed(() => {
  const rows = recentLatency.value
    .filter(
      (item) =>
        typeof item?.model === "string" &&
        item.model.trim().length > 0 &&
        typeof item.timestamp === "string" &&
        item.timestamp.trim().length > 0,
    )
    .sort((left, right) => left.timestamp.localeCompare(right.timestamp));
  const xAxisLabels = rows.map((item) => formatShortTime(item.timestamp));
  const models = [...new Set(rows.map((item) => item.model))].sort((a, b) => a.localeCompare(b));

  return {
    backgroundColor: "transparent",
    textStyle: { color: chartTextColor.value },
    tooltip: {
      trigger: "axis",
      backgroundColor: chartTooltipBackground.value,
      borderColor: chartSplitLineColor.value,
      textStyle: { color: chartTooltipText.value },
      formatter: (params: Array<{
        data: number | null;
        dataIndex: number;
        marker: string;
        seriesName: string;
      }>) => {
        const items = Array.isArray(params) ? params : [params];
        const visibleItems = items.filter((item) => item.data !== null);
        if (visibleItems.length === 0) {
          return "";
        }
        const row = rows[visibleItems[0]?.dataIndex ?? 0];
        const lines = visibleItems.map(
          (item) => `${item.marker}${item.seriesName}: ${Math.round(Number(item.data))} ms`,
        );
        return [formatFullTime(row?.timestamp ?? ""), ...lines].join("<br/>");
      },
    },
    legend: {
      data: models,
      top: "top",
      left: "center",
      textStyle: { color: chartTextColor.value },
    },
    grid: { left: 16, right: 16, top: 48, bottom: 20, containLabel: true },
    xAxis: {
      type: "category",
      data: xAxisLabels,
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
    series: models.map((model, index) => ({
      name: model,
      type: "line",
      smooth: true,
      showSymbol: true,
      symbolSize: 7,
      itemStyle: { color: latencyPalette.value[index % latencyPalette.value.length] },
      lineStyle: { width: 3 },
      data: rows.map((item) => (item.model === model ? item.latency_ms : null)),
    })),
  };
});

const loadRecentLatency = async (): Promise<void> => {
  try {
    const recentLatencyResp = await apiGetJson<{ data: RecentLatencyRow[] }>(
      withToken("dashboard/recent-latency?limit=24"),
    );
    recentLatencyApiAvailable.value = true;
    recentLatency.value = recentLatencyResp.data ?? [];
  } catch (error) {
    // TODO: remove this fallback once every backend deployment exposes /dashboard/recent-latency.
    if (error instanceof ApiClientError && error.status === 404) {
      recentLatencyApiAvailable.value = false;
      recentLatency.value = [];
      return;
    }
    recentLatencyApiAvailable.value = true;
    recentLatency.value = [];
  }
};

const loadRecentIssues = async (): Promise<void> => {
  try {
    const response = await apiGetJson<{ data: RecentIssueRow[] }>(
      withToken(`dashboard/errors/recent?limit=8&level=${issueFilter.value}`),
    );
    recentIssues.value = response.data ?? [];
  } catch {
    recentIssues.value = [];
  }
};

const loadDashboard = async (): Promise<void> => {
  loading.value = true;
  try {
    const [statusResp, tokenResp, latencyResp, tasksResp, skillsResp] = await Promise.all([
      apiGetJson<DashboardStatus>(withToken("dashboard/status")),
      apiGetJson<{ data: TokenStatsRow[] }>(withToken("dashboard/token-stats?days=7")),
      apiGetJson<{ data: ModelLatencyStatsRow[] }>(
        withToken("dashboard/latency-stats?days=7&group_by=model"),
      ),
      apiGetJson<{ data: RecentTaskRow[] }>(withToken("dashboard/recent-tasks?limit=20")),
      apiGetJson<{ data: SkillRow[] }>(withToken("dashboard/skills")),
    ]);
    status.value = statusResp;
    tokenStats.value = tokenResp.data ?? [];
    latencyModelStats.value = latencyResp.data ?? [];
    recentTasks.value = tasksResp.data ?? [];
    skills.value = skillsResp.data ?? [];
    await Promise.all([loadRecentLatency(), loadRecentIssues()]);
  } finally {
    loading.value = false;
  }
};

const loadChannels = async (): Promise<void> => {
  const response = await apiGetJson<ChannelsStatusResponse>(withToken("channels/status"));
  channels.value = response.channels;
};

const createSessionId = (): string => `session-${Date.now()}`;

const scrollToStats = (): void => {
  statsCardRef.value?.scrollIntoView({ behavior: "smooth", block: "start" });
};

const handleQuickAction = async (action: string): Promise<void> => {
  if (action === "reload-config") {
    emit("navigate", "config");
    actionMessage.info("当前未提供快速重载 API，已跳转到 Config。");
    return;
  }
  if (action === "new-chat") {
    emit("open-session", createSessionId());
    return;
  }
  if (action === "view-stats") {
    scrollToStats();
    return;
  }
  if (action === "settings") {
    emit("navigate", "config");
    return;
  }
  actionMessage.info("功能开发中");
};

const quickActions = computed(() => [
  { key: "reload-config", icon: "🔄", label: "重载配置", description: "前往 Config 页面进行检查与保存" },
  { key: "new-chat", icon: "💬", label: "新建对话", description: "创建一个新的会话上下文" },
  { key: "view-stats", icon: "📊", label: "查看统计", description: "滚动到 Dashboard 统计区域" },
  { key: "clear-cache", icon: "🧹", label: "清理缓存", description: "缓存清理入口预留中" },
  { key: "settings", icon: "⚙️", label: "系统设置", description: "进入配置页查看系统设置" },
  { key: "view-logs", icon: "📝", label: "查看日志", description: "日志详情入口预留中" },
]);

onMounted(() => {
  void loadDashboard();
  void loadChannels();
  refreshTimer = setInterval(() => {
    void loadDashboard();
  }, 5000);
  channelRefreshTimer = setInterval(() => {
    void loadChannels();
  }, 30000);
});

watch(issueFilter, () => {
  void loadRecentIssues();
});

onUnmounted(() => {
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
    <n-grid
      class="dashboard-grid"
      :x-gap="12"
      :y-gap="12"
      cols="1 s:1 m:2 l:2"
      responsive="screen"
    >
      <n-grid-item>
        <ChannelStatusCard
          :title="channelCardMap.webui.name"
          :icon="channelCardMap.webui.icon"
          :status-label="channelCardMap.webui.statusLabel"
          :tag-type="channelCardMap.webui.tagType"
          :details="channelCardMap.webui.details"
        />
      </n-grid-item>

      <n-grid-item>
        <div ref="statsCardRef" class="card-anchor">
          <n-card title="系统状态" :bordered="false" class="dashboard-card">
            <div v-if="loading && !status" class="status-skeleton">
              <n-skeleton v-for="index in 4" :key="`status-skeleton-${index}`" height="84px" round />
            </div>
            <div v-else-if="status" class="status-grid">
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
        </div>
      </n-grid-item>

      <n-grid-item>
        <ChannelStatusCard
          :title="channelCardMap.qqBot.name"
          :icon="channelCardMap.qqBot.icon"
          :status-label="channelCardMap.qqBot.statusLabel"
          :tag-type="channelCardMap.qqBot.tagType"
          :details="channelCardMap.qqBot.details"
        />
      </n-grid-item>

      <n-grid-item v-if="channelCardMap.qqNapcat">
        <ChannelStatusCard
          :title="channelCardMap.qqNapcat.name"
          :icon="channelCardMap.qqNapcat.icon"
          :status-label="channelCardMap.qqNapcat.statusLabel"
          :tag-type="channelCardMap.qqNapcat.tagType"
          :details="channelCardMap.qqNapcat.details"
        />
      </n-grid-item>

      <n-grid-item>
        <ChannelStatusCard
          :title="channelCardMap.weixin.name"
          :icon="channelCardMap.weixin.icon"
          :status-label="channelCardMap.weixin.statusLabel"
          :tag-type="channelCardMap.weixin.tagType"
          :details="channelCardMap.weixin.details"
        />
      </n-grid-item>

      <n-grid-item>
        <ChannelStatusCard
          :title="channelCardMap.feishu.name"
          :icon="channelCardMap.feishu.icon"
          :status-label="channelCardMap.feishu.statusLabel"
          :tag-type="channelCardMap.feishu.tagType"
          :details="channelCardMap.feishu.details"
        />
      </n-grid-item>

      <n-grid-item>
        <ChannelStatusCard
          :title="channelCardMap.email.name"
          :icon="channelCardMap.email.icon"
          :status-label="channelCardMap.email.statusLabel"
          :tag-type="channelCardMap.email.tagType"
          :details="channelCardMap.email.details"
        />
      </n-grid-item>

      <n-grid-item>
        <ChannelStatusCard
          :title="channelCardMap.heartbeat.name"
          :icon="channelCardMap.heartbeat.icon"
          :status-label="channelCardMap.heartbeat.statusLabel"
          :tag-type="channelCardMap.heartbeat.tagType"
          :details="channelCardMap.heartbeat.details"
        />
      </n-grid-item>

      <n-grid-item>
        <n-card title="Skills 状态" :bordered="false" class="dashboard-card">
          <div v-if="loading && skills.length === 0" class="skills-skeleton">
            <n-skeleton v-for="index in 5" :key="`skill-skeleton-${index}`" text :repeat="1" />
          </div>
          <ul v-else class="skills-list">
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
        <n-card title="Token 消耗（按模型）" :bordered="false" class="dashboard-card">
          <v-chart autoresize :option="tokenChartOption" :theme="chartTheme" class="chart" />
        </n-card>
      </n-grid-item>

      <n-grid-item span="2 m:2 l:2">
        <n-card :bordered="false" class="dashboard-card analytics-card">
          <template #header>
            <div class="chart-card-header">
              <h3 class="chart-card-title">模型响应延迟统计</h3>
              <p class="chart-card-description">
                各模型调用耗时分布（ms），P50=一半请求低于此值，P95/P99=极端情况
              </p>
            </div>
          </template>
          <div v-if="latencyModelStats.length === 0" class="chart-empty">
            <n-empty description="暂无模型延迟统计数据" />
          </div>
          <v-chart
            v-else
            autoresize
            :option="latencyDistributionOption"
            :theme="chartTheme"
            class="chart chart-lg"
          />
        </n-card>
      </n-grid-item>

      <n-grid-item span="2 m:2 l:2">
        <n-card :bordered="false" class="dashboard-card analytics-card">
          <template #header>
            <div class="chart-card-header">
              <h3 class="chart-card-title">最近调用延迟</h3>
              <p class="chart-card-description">最近调用的实际响应时间（ms），越低越快</p>
            </div>
          </template>
          <div v-if="!recentLatencyApiAvailable" class="chart-empty">
            <n-empty description="需要后端提供原始调用记录 API" />
          </div>
          <div v-else-if="recentLatency.length === 0" class="chart-empty">
            <n-empty description="暂无最近调用延迟数据" />
          </div>
          <v-chart
            v-else
            autoresize
            :option="recentLatencyOption"
            :theme="chartTheme"
            class="chart chart-lg"
          />
        </n-card>
      </n-grid-item>

      <n-grid-item span="2 m:2 l:2">
        <n-card :bordered="false" class="dashboard-card issue-card">
          <template #header>
            <div class="card-header-row">
              <div>
                <h3 class="chart-card-title">最近错误 / 告警</h3>
                <p class="chart-card-description">最近 8 条系统错误与告警，可快速展开查看详情。</p>
              </div>
              <div class="issue-filter-group">
                <button
                  v-for="option in issueFilterOptions"
                  :key="option.value"
                  type="button"
                  class="issue-filter-button"
                  :data-active="option.value === issueFilter"
                  @click="issueFilter = option.value"
                >
                  {{ option.label }}
                </button>
              </div>
            </div>
          </template>
          <div v-if="loading && recentIssues.length === 0" class="issues-skeleton">
            <n-skeleton v-for="index in 5" :key="`issue-skeleton-${index}`" height="56px" round />
          </div>
          <div v-else-if="recentIssues.length === 0" class="empty-sessions">
            <n-empty description="✅ 系统运行正常，暂无错误或告警" />
          </div>
          <ul v-else class="issue-list">
            <li
              v-for="issue in recentIssues"
              :key="`${issue.timestamp}-${issue.level}-${issue.message}`"
              class="issue-item"
              @click="openIssueDetail(issue)"
            >
              <div class="issue-info">
                <div class="issue-head">
                  <span class="issue-level">{{ issueLevelIcon(issue.level) }} {{ issueLevelLabel(issue.level) }}</span>
                  <span class="issue-time">{{ formatRelativeTime(issue.timestamp) }}</span>
                </div>
                <span class="issue-summary">{{ formatIssueSummary(issue.message) }}</span>
              </div>
              <span class="issue-source">{{ issue.source || "system" }}</span>
            </li>
          </ul>
        </n-card>
      </n-grid-item>

      <n-grid-item span="2 m:2 l:2">
        <n-card :bordered="false" class="dashboard-card quick-action-card">
          <template #header>
            <div class="chart-card-header">
              <h3 class="chart-card-title">快捷操作</h3>
              <p class="chart-card-description">常用操作入口，未接通后端的功能会给出开发中提示。</p>
            </div>
          </template>
          <div class="quick-action-grid">
            <button
              v-for="action in quickActions"
              :key="action.key"
              type="button"
              class="quick-action-button"
              @click="void handleQuickAction(action.key)"
            >
              <span class="quick-action-icon">{{ action.icon }}</span>
              <span class="quick-action-copy">
                <strong>{{ action.label }}</strong>
                <small>{{ action.description }}</small>
              </span>
            </button>
          </div>
        </n-card>
      </n-grid-item>
    </n-grid>

    <n-drawer :show="selectedIssue !== null" width="min(560px, 92vw)" @update:show="(show) => !show && closeIssueDetail()">
      <n-drawer-content title="错误 / 告警详情" closable>
        <div v-if="selectedIssue" class="issue-drawer">
          <div class="issue-drawer-meta">
            <n-tag :type="selectedIssue.level === 'error' ? 'error' : 'warning'">
              {{ issueLevelIcon(selectedIssue.level) }} {{ issueLevelLabel(selectedIssue.level) }}
            </n-tag>
            <span>{{ formatFullTime(selectedIssue.timestamp) }}</span>
            <span>{{ selectedIssue.source || "system" }}</span>
          </div>
          <h3 class="issue-drawer-title">{{ selectedIssue.message }}</h3>
          <pre class="issue-drawer-detail">{{ selectedIssue.detail || selectedIssue.message }}</pre>
        </div>
      </n-drawer-content>
    </n-drawer>
  </section>
</template>

<style scoped>
.dashboard-view {
  height: 100%;
  min-height: 0;
  overflow: auto;
  width: 100%;
}

.dashboard-grid :deep(.n-grid-item) {
  display: flex;
}

.dashboard-grid :deep(.n-grid-item > *) {
  flex: 1 1 auto;
  min-width: 0;
}

.dashboard-card {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.dashboard-card :deep(.n-card__content) {
  flex: 1;
}

.card-anchor {
  display: flex;
  width: 100%;
}

.card-header-row {
  align-items: center;
  display: flex;
  gap: 1rem;
  justify-content: space-between;
  width: 100%;
}

.issue-filter-group {
  background: color-mix(in srgb, var(--surface-soft) 92%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 88%, transparent);
  border-radius: 999px;
  display: inline-flex;
  gap: 0.25rem;
  padding: 0.25rem;
}

.issue-filter-button {
  background: transparent;
  border: 0;
  border-radius: 999px;
  color: var(--muted);
  cursor: pointer;
  font-size: 0.78rem;
  font-weight: 700;
  padding: 0.35rem 0.65rem;
}

.issue-filter-button[data-active="true"] {
  background: color-mix(in srgb, var(--brand) 16%, transparent);
  color: var(--text);
}

.analytics-card {
  background:
    linear-gradient(150deg, color-mix(in srgb, var(--brand) 6%, transparent), transparent 68%),
    color-mix(in srgb, var(--surface) 95%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
}

.status-grid {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.status-skeleton,
.issues-skeleton {
  display: grid;
  gap: 0.75rem;
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

.skills-skeleton {
  display: grid;
  gap: 0.7rem;
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

.chart-lg {
  height: 320px;
}

.chart-card-header {
  display: grid;
  gap: 0.3rem;
}

.chart-card-title {
  font-size: 1rem;
  margin: 0;
}

.chart-card-description {
  color: var(--muted);
  font-size: 0.84rem;
  line-height: 1.5;
  margin: 0;
}

.chart-empty {
  align-items: center;
  display: flex;
  height: 320px;
  justify-content: center;
}

.empty-sessions {
  align-items: center;
  display: flex;
  height: 200px;
  justify-content: center;
}

.issue-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  list-style: none;
  margin: 0;
  padding: 0;
}

.issue-card,
.quick-action-card {
  min-height: 320px;
}

.issue-item {
  align-items: center;
  background: color-mix(in srgb, var(--surface-soft) 90%, transparent);
  border: 1px solid var(--panel-edge);
  border-radius: 12px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 12px 14px;
  transition: all 0.2s ease;
}

.issue-item:hover {
  background: color-mix(in srgb, var(--surface) 100%, transparent);
  border-color: color-mix(in srgb, var(--brand) 40%, var(--panel-edge));
  transform: translateY(-1px);
}

.issue-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.issue-head {
  align-items: center;
  display: flex;
  gap: 0.65rem;
  justify-content: space-between;
}

.issue-level,
.issue-time {
  color: var(--muted);
  font-size: 12px;
}

.issue-summary {
  color: var(--text);
  font-size: 0.92rem;
  line-height: 1.45;
  overflow: hidden;
  text-overflow: ellipsis;
}

.issue-source {
  color: var(--muted);
  flex-shrink: 0;
  font-family: "IBM Plex Mono", "Fira Code", monospace;
  font-size: 0.78rem;
}

.quick-action-grid {
  display: grid;
  gap: 0.85rem;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.quick-action-button {
  align-items: flex-start;
  background:
    linear-gradient(145deg, color-mix(in srgb, var(--brand) 8%, transparent), transparent 75%),
    color-mix(in srgb, var(--surface-soft) 92%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 92%, transparent);
  border-radius: 14px;
  color: var(--text);
  cursor: pointer;
  display: grid;
  gap: 0.75rem;
  grid-template-columns: auto 1fr;
  padding: 1rem;
  text-align: left;
}

.quick-action-button:hover {
  border-color: color-mix(in srgb, var(--brand) 45%, var(--panel-edge));
  box-shadow: var(--card-shadow);
  transform: translateY(-1px);
}

.quick-action-icon {
  align-items: center;
  background: color-mix(in srgb, var(--brand) 12%, transparent);
  border-radius: 12px;
  display: inline-flex;
  font-size: 1.2rem;
  height: 2.7rem;
  justify-content: center;
  width: 2.7rem;
}

.quick-action-copy {
  display: grid;
  gap: 0.2rem;
}

.quick-action-copy strong {
  font-size: 0.93rem;
}

.quick-action-copy small {
  color: var(--muted);
  line-height: 1.4;
}

.issue-drawer {
  display: grid;
  gap: 0.9rem;
}

.issue-drawer-meta {
  align-items: center;
  color: var(--muted);
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
}

.issue-drawer-title {
  font-size: 1.05rem;
  margin: 0;
}

.issue-drawer-detail {
  background: color-mix(in srgb, var(--surface-soft) 92%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 88%, transparent);
  border-radius: 12px;
  margin: 0;
  max-height: 60vh;
  overflow: auto;
  padding: 1rem;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 767px) {
  .card-header-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .status-grid {
    grid-template-columns: 1fr;
  }

  .quick-action-grid {
    grid-template-columns: 1fr;
  }

  .issue-item {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
