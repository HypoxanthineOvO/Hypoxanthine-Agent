<script setup lang="ts">
import {
  NButton,
  NConfigProvider,
  NGlobalStyle,
  NMessageProvider,
  NNotificationProvider,
} from "naive-ui";
import type { GlobalThemeOverrides } from "naive-ui";
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref, watch } from "vue";

import SideNav from "./components/layout/SideNav.vue";
import { useThemeMode } from "./composables/useThemeMode";

const ChatView = defineAsyncComponent(() => import("./views/ChatView.vue"));
const DashboardView = defineAsyncComponent(() => import("./views/DashboardView.vue"));
const ConfigView = defineAsyncComponent(() => import("./views/ConfigView.vue"));
const MemoryView = defineAsyncComponent(() => import("./views/MemoryView.vue"));

const fallbackWsUrl = (() => {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
})();

const wsUrl = import.meta.env.VITE_WS_URL ?? fallbackWsUrl;
const token = import.meta.env.VITE_WS_TOKEN ?? "dev-token-change-me";
const apiBase = import.meta.env.VITE_API_BASE ?? "";

const { mode, theme, toggleMode } = useThemeMode();
const activeView = ref<"chat" | "dashboard" | "config" | "memory">("chat");
const activeChatSessionId = ref<string>("");

const onOpenSession = (sessionId: string) => {
  activeView.value = "chat";
  activeChatSessionId.value = sessionId;
};

const onNavigate = (
  view: "chat" | "dashboard" | "config" | "memory",
  sessionId?: string,
): void => {
  activeView.value = view;
  if (view === "chat" && sessionId) {
    activeChatSessionId.value = sessionId;
  }
};
const navItems = [
  { key: "chat", icon: "💬", label: "Chat" },
  { key: "dashboard", icon: "📊", label: "Dashboard" },
  { key: "config", icon: "⚙️", label: "Config" },
  { key: "memory", icon: "🧠", label: "Memory" },
] as const;

const viewportWidth = ref(window.innerWidth);
const sidebarCollapsed = ref(false);

const isMobile = computed(() => viewportWidth.value < 768);
const isDesktop = computed(() => viewportWidth.value >= 1024);
const isTablet = computed(
  () => viewportWidth.value >= 768 && viewportWidth.value < 1024,
);
const showSideNav = computed(() => viewportWidth.value >= 768);
const collapsed = computed(() => (isTablet.value ? sidebarCollapsed.value : false));

const updateViewportWidth = (): void => {
  viewportWidth.value = window.innerWidth;
};

const toggleSidebar = (): void => {
  if (!isTablet.value) {
    return;
  }
  sidebarCollapsed.value = !sidebarCollapsed.value;
};

const onSidebarCollapseEvent = (): void => {
  if (isTablet.value) {
    sidebarCollapsed.value = true;
  }
};

const titleByView: Record<typeof activeView.value, string> = {
  chat: "Hypo-Agent · Chat Workspace",
  dashboard: "Hypo-Agent · Dashboard",
  config: "Hypo-Agent · Config Editor",
  memory: "Hypo-Agent · Memory Editor",
};

const pageTitle = computed(() => titleByView[activeView.value]);

const palette = computed(() =>
  mode.value === "dark"
    ? {
        bg: "#11131a",
        surface: "#1a1f2b",
        surfaceSoft: "#212736",
        panelEdge: "#2f394d",
        text: "#eef3ff",
        textSoft: "#c6d1e6",
        muted: "#9eacc7",
        brand: "#5aa6ff",
        brandStrong: "#8bbcff",
        brandAlt: "#2d8cff",
        info: "#6ea8ff",
        ok: "#5bd08d",
        warn: "#ffb84d",
        error: "#ff6b81",
      }
    : {
        bg: "#f5f7fb",
        surface: "#ffffff",
        surfaceSoft: "#f8fafc",
        panelEdge: "#d7e0ec",
        text: "#162033",
        textSoft: "#54627a",
        muted: "#687892",
        brand: "#2080f0",
        brandStrong: "#155ec2",
        brandAlt: "#5aa6ff",
        info: "#2080f0",
        ok: "#18a058",
        warn: "#f0a020",
        error: "#d03050",
      },
);

const themeOverrides = computed<GlobalThemeOverrides>(() => ({
  common: {
    borderRadius: "14px",
    borderRadiusSmall: "12px",
    primaryColor: palette.value.brand,
    primaryColorHover: palette.value.brandAlt,
    primaryColorPressed: palette.value.brandStrong,
    primaryColorSuppl: palette.value.brandAlt,
    infoColor: palette.value.info,
    infoColorHover: palette.value.brandAlt,
    successColor: palette.value.ok,
    warningColor: palette.value.warn,
    errorColor: palette.value.error,
    bodyColor: palette.value.bg,
    cardColor: palette.value.surface,
    modalColor: palette.value.surface,
    popoverColor: palette.value.surface,
    tableColor: palette.value.surface,
    borderColor: palette.value.panelEdge,
    textColorBase: palette.value.text,
    textColor1: palette.value.text,
    textColor2: palette.value.textSoft,
    textColor3: palette.value.muted,
    placeholderColor: palette.value.muted,
    closeIconColor: palette.value.muted,
    closeIconColorHover: palette.value.text,
  },
  Card: {
    borderRadius: "14px",
    borderRadiusSmall: "12px",
    borderRadiusMedium: "14px",
    borderRadiusLarge: "16px",
    boxShadow:
      mode.value === "dark"
        ? "0 0 0 1px rgba(148, 163, 184, 0.14), 0 16px 30px rgba(2, 6, 23, 0.22)"
        : "0 18px 40px rgba(15, 23, 42, 0.08)",
  },
  Button: {
    borderRadiusMedium: "12px",
    borderRadiusSmall: "10px",
    textColorText: palette.value.text,
    textColorGhost: palette.value.text,
    textColorTertiary: palette.value.text,
  },
  Input: {
    color: palette.value.surface,
    colorFocus: palette.value.surface,
    border: `1px solid ${palette.value.panelEdge}`,
    borderHover: `1px solid ${palette.value.brandAlt}`,
    borderFocus: `1px solid ${palette.value.brand}`,
    caretColor: palette.value.brand,
    textColor: palette.value.text,
    placeholderColor: palette.value.muted,
  },
  Menu: {
    itemTextColor: palette.value.textSoft,
    itemTextColorHover: palette.value.text,
    itemTextColorActive: palette.value.text,
    itemColorHover: mode.value === "dark" ? "rgba(90, 166, 255, 0.12)" : "rgba(32, 128, 240, 0.12)",
    itemColorActive: mode.value === "dark" ? "rgba(90, 166, 255, 0.18)" : "rgba(32, 128, 240, 0.18)",
    itemColorActiveHover: mode.value === "dark" ? "rgba(90, 166, 255, 0.22)" : "rgba(32, 128, 240, 0.22)",
    itemIconColor: palette.value.muted,
    itemIconColorActive: palette.value.brand,
    itemIconColorHover: palette.value.brand,
    arrowColor: palette.value.muted,
    borderRadius: "12px",
  },
  Layout: {
    color: "transparent",
    siderColor: "transparent",
    headerColor: "transparent",
  },
  Drawer: {
    color: palette.value.surface,
  },
}));

const onSelectView = (view: "chat" | "dashboard" | "config" | "memory"): void => {
  activeView.value = view;
};

watch(mode, (nextMode) => {
  document.documentElement.dataset.theme = nextMode;
}, { immediate: true });

watch(pageTitle, (nextTitle) => {
  document.title = nextTitle;
}, { immediate: true });

watch(isDesktop, (nextIsDesktop) => {
  if (nextIsDesktop) {
    sidebarCollapsed.value = false;
  }
});

watch(isTablet, (nextIsTablet, prevIsTablet) => {
  if (nextIsTablet && !prevIsTablet) {
    sidebarCollapsed.value = true;
  }
});

watch(showSideNav, (visible) => {
  if (!visible) {
    sidebarCollapsed.value = true;
  }
});

onMounted(() => {
  window.addEventListener("resize", updateViewportWidth);
  window.addEventListener("hypo:sidebar-collapse", onSidebarCollapseEvent);
});

onUnmounted(() => {
  window.removeEventListener("resize", updateViewportWidth);
  window.removeEventListener("hypo:sidebar-collapse", onSidebarCollapseEvent);
});
</script>

<template>
  <n-config-provider :theme="theme" :theme-overrides="themeOverrides">
    <n-notification-provider>
      <n-message-provider>
        <n-global-style />
        <div class="app-shell">
          <aside v-if="showSideNav" class="app-rail">
            <SideNav
              :collapsed="collapsed"
              :active="activeView"
              @select="onSelectView"
            />
          </aside>

          <main class="app-main">
            <header class="main-header">
              <div class="header-left">
                <n-button
                  v-if="isTablet"
                  size="small"
                  tertiary
                  @click="toggleSidebar"
                >
                  {{ collapsed ? "展开导航" : "折叠导航" }}
                </n-button>
                <p class="header-title">{{ pageTitle }}</p>
              </div>

              <n-button size="small" tertiary @click="toggleMode">
                切换 {{ mode === "dark" ? "Light" : "Dark" }}
              </n-button>
            </header>

            <section class="main-body">
              <Transition name="view-fade" mode="out-in">
                <ChatView
                  v-if="activeView === 'chat'"
                  key="chat"
                  :session-id="activeChatSessionId"
                  :ws-url="wsUrl"
                  :token="token"
                  :api-base="apiBase"
                />
                <DashboardView
                  v-else-if="activeView === 'dashboard'"
                  key="dashboard"
                  :token="token"
                  :api-base="apiBase"
                  @open-session="onOpenSession"
                  @navigate="onNavigate"
                />
                <ConfigView
                  v-else-if="activeView === 'config'"
                  key="config"
                  :token="token"
                  :api-base="apiBase"
                />
                <MemoryView
                  v-else
                  key="memory"
                  :token="token"
                  :api-base="apiBase"
                />
              </Transition>
            </section>

            <nav v-if="isMobile" class="mobile-nav" data-testid="mobile-nav">
              <button
                v-for="item in navItems"
                :key="item.key"
                type="button"
                class="mobile-nav-item"
                :data-active="item.key === activeView"
                @click="onSelectView(item.key)"
              >
                <span class="mobile-nav-icon">{{ item.icon }}</span>
                <span class="mobile-nav-label">{{ item.label }}</span>
              </button>
            </nav>
          </main>
        </div>
      </n-message-provider>
    </n-notification-provider>
  </n-config-provider>
</template>

<style scoped>
.app-shell {
  display: grid;
  gap: 0.85rem;
  grid-template-columns: auto 1fr;
  margin: 0 auto;
  max-width: 1320px;
  height: 100vh;
  min-height: 0;
  padding: 0.85rem;
}

.app-rail {
  min-height: 0;
  max-width: 250px;
  min-width: 76px;
  transition:
    max-width 0.3s ease,
    min-width 0.3s ease,
    transform 0.3s ease;
}

.app-main {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
  height: 100%;
  min-height: 0;
  min-width: 0;
}

.main-header {
  align-items: center;
  backdrop-filter: blur(6px);
  background: color-mix(in srgb, var(--panel) 86%, transparent);
  border: 1px solid var(--panel-edge);
  border-radius: 0.9rem;
  display: flex;
  justify-content: space-between;
  min-height: 3.2rem;
  padding: 0.6rem 0.8rem;
}

.header-left {
  align-items: center;
  display: flex;
  gap: 0.65rem;
}

.header-title {
  font-size: 0.92rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  margin: 0;
}

.main-body {
  display: flex;
  flex: 1;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
  width: 100%;
}

.main-body :deep(> *) {
  flex: 1 1 auto;
  min-width: 0;
}

.mobile-nav {
  background: color-mix(in srgb, var(--panel) 92%, transparent);
  border: 1px solid var(--panel-edge);
  border-radius: 1rem;
  display: grid;
  gap: 0.45rem;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  padding: 0.45rem;
}

.mobile-nav-item {
  align-items: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 0.8rem;
  color: var(--text);
  cursor: pointer;
  display: grid;
  gap: 0.2rem;
  justify-items: center;
  padding: 0.45rem 0.35rem;
}

.mobile-nav-item[data-active="true"] {
  background: color-mix(in srgb, var(--brand) 20%, transparent);
  border-color: color-mix(in srgb, var(--brand) 55%, var(--panel-edge));
}

.mobile-nav-icon {
  font-size: 1rem;
  line-height: 1;
}

.mobile-nav-label {
  font-size: 0.74rem;
  font-weight: 700;
}

.view-fade-enter-active,
.view-fade-leave-active {
  transition:
    opacity 0.24s ease,
    transform 0.24s ease;
}

.view-fade-enter-from,
.view-fade-leave-to {
  opacity: 0;
  transform: translateY(10px);
}

@media (max-width: 1023px) {
  .app-shell {
    grid-template-columns: 76px 1fr;
  }

  .app-rail {
    max-width: 76px;
    min-width: 76px;
  }
}

@media (max-width: 767px) {
  .app-shell {
    grid-template-columns: 1fr;
    padding: 0.5rem;
    height: 100dvh;
  }

  .main-header {
    border-radius: 0.75rem;
    padding: 0.55rem 0.7rem;
  }

  .header-title {
    font-size: 0.86rem;
  }

  .main-body {
    overflow: hidden;
  }
}

@media (prefers-reduced-motion: reduce) {
  .app-rail,
  .view-fade-enter-active,
  .view-fade-leave-active {
    transition: none;
  }
}
</style>
