<script setup lang="ts">
import {
  NButton,
  NCard,
  NEmpty,
  NSpin,
  NTabPane,
  NTabs,
  useMessage,
} from "naive-ui";
import { computed, onMounted, ref, watch } from "vue";
import YAML from "yaml";

import ConfigFileList, { type ConfigFileMeta } from "../components/ConfigFileList.vue";
import ConfigFormRenderer from "../components/ConfigFormRenderer.vue";
import MonacoEditor from "../components/editor/MonacoEditor.vue";
import { apiGetJson, ApiClientError } from "../utils/apiClient";

const props = withDefaults(
  defineProps<{
    token: string;
    apiBase?: string;
  }>(),
  {
    apiBase: "",
  },
);

interface ConfigFileResponse {
  filename: string;
  content: string;
  data: Record<string, unknown>;
  masked_fields: string[];
  reloaded?: boolean;
}

const message = (() => {
  try {
    return useMessage();
  } catch {
    return {
      success: (_text: string) => undefined,
      error: (_text: string) => undefined,
      warning: (_text: string) => undefined,
    };
  }
})();

const files = ref<ConfigFileMeta[]>([]);
const selectedFilename = ref("persona.yaml");
const activeMode = ref<"form" | "yaml">("form");
const loadingList = ref(false);
const loadingFile = ref(false);
const saving = ref(false);
const rawContent = ref("");
const draftData = ref<Record<string, unknown>>({});
const maskedFields = ref<string[]>([]);
const preserveRawContent = ref(false);

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

const selectedFileMeta = computed(
  () => files.value.find((item) => item.filename === selectedFilename.value) ?? null,
);

const syncRawFromData = (value: Record<string, unknown>): void => {
  rawContent.value = YAML.stringify(value);
};

const parseRawContent = (): Record<string, unknown> | null => {
  try {
    const parsed = YAML.parse(rawContent.value) ?? {};
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      message.error("YAML 根节点必须是对象");
      return null;
    }
    return parsed as Record<string, unknown>;
  } catch (error) {
    message.error(`YAML 解析失败: ${error instanceof Error ? error.message : String(error)}`);
    return null;
  }
};

const applyResponse = (payload: ConfigFileResponse): void => {
  draftData.value = payload.data ?? {};
  maskedFields.value = Array.isArray(payload.masked_fields) ? payload.masked_fields : [];
  rawContent.value = payload.content ?? YAML.stringify(payload.data ?? {});
};

const loadFiles = async (): Promise<void> => {
  loadingList.value = true;
  try {
    const response = await apiGetJson<ConfigFileMeta[]>(withToken("config"));
    files.value = response;
    if (!files.value.some((item) => item.filename === selectedFilename.value)) {
      selectedFilename.value = files.value[0]?.filename ?? "persona.yaml";
    }
  } catch (error) {
    const detail = error instanceof ApiClientError ? error.message : String(error);
    message.error(`加载配置列表失败: ${detail}`);
    files.value = [];
  } finally {
    loadingList.value = false;
  }
};

const loadSelectedFile = async (): Promise<void> => {
  if (!selectedFilename.value) {
    return;
  }
  loadingFile.value = true;
  try {
    const response = await apiGetJson<ConfigFileResponse>(
      withToken(`config/${selectedFilename.value}`),
    );
    applyResponse(response);
  } catch (error) {
    const detail = error instanceof ApiClientError ? error.message : String(error);
    message.error(`加载配置失败: ${detail}`);
    draftData.value = {};
    maskedFields.value = [];
    rawContent.value = "";
  } finally {
    loadingFile.value = false;
  }
};

const updateDraftData = (value: unknown): void => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return;
  }
  draftData.value = value as Record<string, unknown>;
  if (activeMode.value === "form") {
    syncRawFromData(draftData.value);
  }
};

const switchToYamlMode = (): void => {
  if (activeMode.value === "yaml") {
    activeMode.value = "form";
    return;
  }
  syncRawFromData(draftData.value);
  activeMode.value = "yaml";
};

const saveCurrentFile = async (): Promise<void> => {
  if (!selectedFilename.value) {
    return;
  }

  const payload =
    activeMode.value === "form"
      ? { data: draftData.value }
      : { content: rawContent.value };

  if (activeMode.value === "yaml") {
    const parsed = parseRawContent();
    if (parsed === null) {
      return;
    }
  }

  saving.value = true;
  try {
    const response = await fetch(withToken(`config/${selectedFilename.value}`), {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({ detail: "保存失败" }));
      const messageText =
        typeof detail.detail === "string" ? detail.detail : JSON.stringify(detail.detail);
      throw new Error(messageText);
    }

    const nextPayload = (await response.json()) as ConfigFileResponse;
    applyResponse(nextPayload);
    message.success("配置保存成功");
  } catch (error) {
    message.error(`保存失败: ${error instanceof Error ? error.message : String(error)}`);
  } finally {
    saving.value = false;
  }
};

const resetCurrentFile = async (): Promise<void> => {
  await loadSelectedFile();
  message.success("已恢复到服务器上的当前版本");
};

onMounted(async () => {
  await loadFiles();
  await loadSelectedFile();
});

watch(selectedFilename, () => {
  activeMode.value = "form";
  void loadSelectedFile();
});

watch(activeMode, (nextMode, previousMode) => {
  if (nextMode === "yaml") {
    if (preserveRawContent.value) {
      preserveRawContent.value = false;
      return;
    }
    syncRawFromData(draftData.value);
    return;
  }
  if (previousMode !== "yaml") {
    return;
  }
  const parsed = parseRawContent();
  if (parsed === null) {
    preserveRawContent.value = true;
    activeMode.value = "yaml";
    return;
  }
  draftData.value = parsed;
});
</script>

<template>
  <section class="config-page">
    <ConfigFileList
      :files="files"
      :selected-filename="selectedFilename"
      @update:selected-filename="selectedFilename = $event"
    />

    <n-card class="config-editor-card" :bordered="false">
      <template #header>
        <div class="editor-header">
          <div>
            <p class="editor-eyebrow">Config Editor</p>
            <h1>{{ selectedFileMeta?.icon }} {{ selectedFileMeta?.label ?? selectedFilename }}</h1>
            <p class="editor-description">
              {{ selectedFileMeta?.description ?? "编辑当前配置文件，并在保存后热重载。" }}
            </p>
          </div>

          <div class="editor-actions">
            <n-button
              secondary
              type="default"
              data-testid="toggle-yaml-mode"
              @click="switchToYamlMode"
            >
              {{ activeMode === "yaml" ? "返回结构化" : "高级编辑" }}
            </n-button>
            <n-button
              secondary
              type="default"
              data-testid="config-reset"
              :disabled="loadingFile || saving"
              @click="resetCurrentFile"
            >
              重置
            </n-button>
            <n-button
              type="primary"
              data-testid="config-save"
              :loading="saving"
              :disabled="loadingFile"
              @click="saveCurrentFile"
            >
              保存
            </n-button>
          </div>
        </div>
      </template>

      <n-spin :show="loadingList || loadingFile">
        <n-empty
          v-if="files.length === 0 && !loadingList"
          description="没有可编辑的配置文件"
        />

        <div v-else class="editor-body">
          <n-tabs v-model:value="activeMode" type="line" animated>
            <n-tab-pane name="form" tab="结构化">
              <ConfigFormRenderer
                :key="selectedFilename"
                :model-value="draftData"
                :masked-fields="maskedFields"
                :file-name="selectedFilename"
                @update:model-value="updateDraftData"
              />
            </n-tab-pane>

            <n-tab-pane name="yaml" tab="原始 YAML">
              <MonacoEditor
                v-model="rawContent"
                language="yaml"
                height="560px"
              />
            </n-tab-pane>
          </n-tabs>
        </div>
      </n-spin>
    </n-card>
  </section>
</template>

<style scoped>
.config-page {
  display: grid;
  gap: 1rem;
  grid-template-columns: minmax(270px, 320px) minmax(0, 1fr);
  height: 100%;
  min-height: 0;
}

.config-editor-card {
  background:
    radial-gradient(
      120% 120% at 0% 0%,
      color-mix(in srgb, var(--brand) 12%, transparent),
      transparent 58%
    ),
    linear-gradient(155deg, color-mix(in srgb, var(--surface) 94%, transparent), transparent 70%),
    color-mix(in srgb, var(--panel) 94%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
  border-radius: 1.1rem;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
}

.editor-header {
  align-items: flex-start;
  display: flex;
  gap: 1rem;
  justify-content: space-between;
}

.editor-eyebrow {
  color: var(--muted);
  font-size: 0.78rem;
  letter-spacing: 0.12em;
  margin: 0 0 0.25rem;
  text-transform: uppercase;
}

.editor-header h1 {
  font-size: clamp(1.35rem, 2.5vw, 2rem);
  margin: 0;
}

.editor-description {
  color: var(--muted);
  line-height: 1.6;
  margin: 0.45rem 0 0;
  max-width: 48rem;
}

.editor-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.65rem;
  justify-content: flex-end;
}

.editor-body {
  min-height: 0;
}

@media (max-width: 900px) {
  .config-page {
    grid-template-columns: 1fr;
  }

  .editor-header {
    flex-direction: column;
  }

  .editor-actions {
    justify-content: flex-start;
  }
}
</style>
