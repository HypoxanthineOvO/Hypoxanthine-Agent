export interface DashboardStatus {
  uptime_seconds: number;
  uptime_human: string;
  session_count: number;
  kill_switch: boolean;
  bwrap_available: boolean;
}

export interface TokenStatsRow {
  date: string;
  model: string;
  total_tokens: number;
}

export interface ModelLatencyStatsRow {
  model: string;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
}

export interface RecentLatencyRow {
  model: string;
  latency_ms: number;
  timestamp: string;
  session_id?: string;
}

export interface RecentTaskRow {
  id: number;
  created_at: string;
  tool_name: string;
  status: string;
  duration_ms: number | null;
}

export interface SkillRow {
  name: string;
  status: "healthy" | "open" | "disabled";
  tools: string[];
}

export interface RecentIssueRow {
  timestamp: string;
  level: "error" | "warning";
  message: string;
  detail: string;
  source: string;
}

export interface WebUiChannelStatus {
  status: string;
  active_connections: number;
  last_message_at: string | null;
}

export interface QQBotChannelStatus {
  status: string;
  qq_bot_enabled?: boolean;
  qq_bot_app_id?: string;
  ws_connected?: boolean;
  connected_at?: string | null;
  last_message_at: string | null;
  messages_received: number;
  messages_sent: number;
}

export interface QQNapcatChannelStatus {
  status: string;
  bot_qq: string;
  napcat_ws_url: string;
  connected_at?: string | null;
  last_message_at: string | null;
  messages_received: number;
  messages_sent: number;
  online?: boolean | null;
  good?: boolean | null;
}

export interface WeixinChannelStatus {
  status: string;
  bot_id: string;
  user_id: string;
  last_message_at: string | null;
  messages_received: number;
  messages_sent: number;
}

export interface FeishuChannelStatus {
  status: string;
  app_id: string;
  chat_count: number;
  last_message_at: string | null;
  messages_received: number;
  messages_sent: number;
}

export interface EmailChannelStatus {
  status: string;
  accounts: string[];
  last_scan_at: string | null;
  next_scan_at: string | null;
  emails_processed: number;
}

export interface HeartbeatChannelStatus {
  status: string;
  last_heartbeat_at: string | null;
  active_tasks: number;
}

export interface ChannelsStatusResponse {
  channels: {
    webui: WebUiChannelStatus;
    qq_bot: QQBotChannelStatus;
    qq_napcat?: QQNapcatChannelStatus;
    weixin: WeixinChannelStatus;
    feishu: FeishuChannelStatus;
    email: EmailChannelStatus;
    heartbeat: HeartbeatChannelStatus;
  };
}

export interface ChannelCard {
  key: string;
  icon: string;
  name: string;
  status: string;
  statusLabel: string;
  tagType: "success" | "warning" | "error" | "default";
  details: string[];
}

export interface ChannelCardMap {
  webui: ChannelCard;
  qqBot: ChannelCard;
  qqNapcat: ChannelCard | null;
  weixin: ChannelCard;
  feishu: ChannelCard;
  email: ChannelCard;
  heartbeat: ChannelCard;
}
