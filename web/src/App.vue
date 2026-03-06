<script setup lang="ts">
import {
  NButton,
  NConfigProvider,
  NGlobalStyle,
  NNotificationProvider,
} from "naive-ui";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";

import SideNav from "./components/layout/SideNav.vue";
import { useThemeMode } from "./composables/useThemeMode";
import ChatView from "./views/ChatView.vue";

const fallbackWsUrl = (() => {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
})();

const wsUrl = import.meta.env.VITE_WS_URL ?? fallbackWsUrl;
const token = import.meta.env.VITE_WS_TOKEN ?? "dev-token-change-me";
const apiBase = import.meta.env.VITE_API_BASE ?? "";

const { mode, theme, toggleMode } = useThemeMode();

const viewportWidth = ref(window.innerWidth);
const sidebarCollapsed = ref(false);

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

const onThemeToggleEvent = (): void => {
  toggleMode();
};

const onSidebarCollapseEvent = (): void => {
  if (isTablet.value) {
    sidebarCollapsed.value = true;
  }
};

watch(mode, (nextMode) => {
  document.documentElement.dataset.theme = nextMode;
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
  window.addEventListener("hypo:theme-toggle", onThemeToggleEvent);
  window.addEventListener("hypo:sidebar-collapse", onSidebarCollapseEvent);
});

onUnmounted(() => {
  window.removeEventListener("resize", updateViewportWidth);
  window.removeEventListener("hypo:theme-toggle", onThemeToggleEvent);
  window.removeEventListener("hypo:sidebar-collapse", onSidebarCollapseEvent);
});
</script>

<template>
  <n-config-provider :theme="theme">
    <n-notification-provider>
      <n-global-style />
      <div class="app-shell">
        <aside v-if="showSideNav" class="app-rail">
          <SideNav :collapsed="collapsed" active="chat" />
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
              <p class="header-title">Hypo-Agent · Chat Workspace</p>
            </div>

            <n-button size="small" tertiary @click="toggleMode">
              切换 {{ mode === "dark" ? "Light" : "Dark" }}
            </n-button>
          </header>

          <section class="main-body">
            <ChatView :ws-url="wsUrl" :token="token" :api-base="apiBase" />
          </section>
        </main>
      </div>
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
  padding: 0.85rem;
}

.app-rail {
  max-width: 250px;
  min-width: 76px;
}

.app-main {
  display: grid;
  gap: 0.85rem;
  grid-template-rows: auto 1fr;
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
  min-height: 0;
  min-width: 0;
  overflow: hidden;
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
  }

  .main-header {
    border-radius: 0.75rem;
    padding: 0.55rem 0.7rem;
  }

  .header-title {
    font-size: 0.86rem;
  }
}
</style>
