<script setup lang="ts">
import {
  NButton,
  NCard,
  NCheckbox,
  NCheckboxGroup,
  NInput,
  NInputNumber,
  NSpace,
  NSwitch,
  NTabPane,
  NTabs,
  useMessage,
} from "naive-ui";
import { computed, onMounted, ref, watch } from "vue";
import YAML from "yaml";

import MonacoEditor from "../components/editor/MonacoEditor.vue";
import { apiGetJson } from "../utils/apiClient";

const props = withDefaults(
  defineProps<{
    token: string;
    apiBase?: string;
  }>(),
  {
    apiBase: "",
  },
);

interface ConfigFileItem {
  filename: string;
  exists: boolean;
  editable: boolean;
}

interface ModelRow {
  name: string;
  provider: string;
  litellm_model: string;
  fallback: string;
  supports_tool_calling: boolean;
  context_window: number | null;
}

type RulePermission = "read" | "write" | "execute";

interface WhitelistRule {
  path: string;
  permissions: RulePermission[];
}

const message = (() => {
  try {
    return useMessage();
  } catch {
    return {
      success: () => undefined,
      error: () => undefined,
      warning: () => undefined,
    };
  }
})();
const loading = ref(false);
const files = ref<ConfigFileItem[]>([]);
const selectedFile = ref("models.yaml");
const activeTab = ref<"form" | "yaml">("form");
const yamlContent = ref("");
const parsedConfig = ref<Record<string, unknown>>({});

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

const refreshYamlFromParsed = (): void => {
  yamlContent.value = YAML.stringify(parsedConfig.value);
};

const tryParseYamlContent = (): void => {
  try {
    const parsed = YAML.parse(yamlContent.value) ?? {};
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      parsedConfig.value = parsed as Record<string, unknown>;
      return;
    }
  } catch {
    // Keep old parsed state when YAML is invalid during typing.
  }
};

const loadFiles = async (): Promise<void> => {
  const response = await apiGetJson<{ files: ConfigFileItem[] }>(withToken("config/files"));
  files.value = response.files;
  if (!files.value.some((item) => item.filename === selectedFile.value) && files.value.length > 0) {
    selectedFile.value = files.value[0]?.filename ?? "models.yaml";
  }
};

const loadSelectedFile = async (): Promise<void> => {
  loading.value = true;
  try {
    const response = await apiGetJson<{ filename: string; content: string }>(
      withToken(`config/${selectedFile.value}`),
    );
    yamlContent.value = response.content;
    tryParseYamlContent();
  } finally {
    loading.value = false;
  }
};

const saveCurrentFile = async (): Promise<void> => {
  loading.value = true;
  try {
    const response = await fetch(withToken(`config/${selectedFile.value}`), {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        content: yamlContent.value,
      }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({ detail: "保存失败" }));
      const detail = typeof payload.detail === "string"
        ? payload.detail
        : JSON.stringify(payload.detail);
      throw new Error(detail);
    }
    message.success("配置保存成功并已热重载");
    tryParseYamlContent();
  } catch (error) {
    message.error(`保存失败: ${error instanceof Error ? error.message : String(error)}`);
  } finally {
    loading.value = false;
  }
};

const models = computed<ModelRow[]>(() => {
  const raw = (parsedConfig.value.models ?? {}) as Record<string, Record<string, unknown>>;
  return Object.entries(raw).map(([name, config]) => ({
    name,
    provider: String(config.provider ?? ""),
    litellm_model: String(config.litellm_model ?? ""),
    fallback: String(config.fallback ?? ""),
    supports_tool_calling: Boolean(config.supports_tool_calling),
    context_window: typeof config.context_window === "number"
      ? config.context_window
      : Number.isFinite(Number(config.context_window))
        ? Number(config.context_window)
        : null,
  }));
});

const taskRouting = computed(() => {
  const raw = (parsedConfig.value.task_routing ?? {}) as Record<string, string>;
  return Object.entries(raw).map(([task, model]) => ({ task, model }));
});

const skills = computed(() => {
  const raw = (parsedConfig.value.skills ?? {}) as Record<string, { enabled?: boolean }>;
  return Object.entries(raw).map(([name, config]) => ({ name, enabled: !!config?.enabled }));
});

const whitelistPermissionOptions: RulePermission[] = ["read", "write", "execute"];

const whitelistRules = computed<WhitelistRule[]>(() => {
  const root = parsedConfig.value as Record<string, any>;
  root.directory_whitelist ??= {};
  const whitelist = root.directory_whitelist as Record<string, any>;
  whitelist.rules ??= [];
  if (!Array.isArray(whitelist.rules)) {
    whitelist.rules = [];
  }

  whitelist.rules = whitelist.rules.map((rule: unknown) => {
    const rawRule = (typeof rule === "object" && rule !== null) ? rule as Record<string, unknown> : {};
    const permissions = Array.isArray(rawRule.permissions)
      ? rawRule.permissions
          .map((item) => String(item))
          .filter((item): item is RulePermission => whitelistPermissionOptions.includes(item as RulePermission))
      : [];
    return {
      path: String(rawRule.path ?? ""),
      permissions,
    };
  });
  return whitelist.rules as WhitelistRule[];
});

const updateModelField = (name: string, field: string, value: string): void => {
  const root = parsedConfig.value as Record<string, any>;
  root.models ??= {};
  root.models[name] ??= {};
  root.models[name][field] = value;
  refreshYamlFromParsed();
};

const updateModelToolCalling = (name: string, enabled: boolean): void => {
  const root = parsedConfig.value as Record<string, any>;
  root.models ??= {};
  root.models[name] ??= {};
  root.models[name].supports_tool_calling = enabled;
  refreshYamlFromParsed();
};

const updateModelContextWindow = (name: string, value: number | null): void => {
  const root = parsedConfig.value as Record<string, any>;
  root.models ??= {};
  root.models[name] ??= {};
  root.models[name].context_window = value === null ? null : Number(value);
  refreshYamlFromParsed();
};

const updateTaskRouting = (task: string, value: string): void => {
  const root = parsedConfig.value as Record<string, any>;
  root.task_routing ??= {};
  root.task_routing[task] = value;
  refreshYamlFromParsed();
};

const updateSkillEnabled = (name: string, enabled: boolean): void => {
  const root = parsedConfig.value as Record<string, any>;
  root.skills ??= {};
  root.skills[name] ??= {};
  root.skills[name].enabled = enabled;
  refreshYamlFromParsed();
};

const updateBreakerNumber = (
  field: "tool_level_max_failures" | "session_level_max_failures" | "cooldown_seconds",
  value: number | null,
): void => {
  const root = parsedConfig.value as Record<string, any>;
  root.circuit_breaker ??= {};
  root.circuit_breaker[field] = Number(value ?? 0);
  refreshYamlFromParsed();
};

const updateWhitelistRulePath = (index: number, path: string): void => {
  const rule = whitelistRules.value[index];
  if (!rule) {
    return;
  }
  rule.path = path;
  refreshYamlFromParsed();
};

const updateWhitelistRulePermissions = (index: number, values: Array<string | number>): void => {
  const rule = whitelistRules.value[index];
  if (!rule) {
    return;
  }
  rule.permissions = values
    .map((item) => String(item))
    .filter((item): item is RulePermission => whitelistPermissionOptions.includes(item as RulePermission));
  refreshYamlFromParsed();
};

const addWhitelistRule = (): void => {
  whitelistRules.value.push({
    path: "",
    permissions: [],
  });
  refreshYamlFromParsed();
};

const removeWhitelistRule = (index: number): void => {
  if (index < 0 || index >= whitelistRules.value.length) {
    return;
  }
  whitelistRules.value.splice(index, 1);
  refreshYamlFromParsed();
};

onMounted(async () => {
  await loadFiles();
  await loadSelectedFile();
});

watch(selectedFile, () => {
  void loadSelectedFile();
});

watch(yamlContent, () => {
  if (activeTab.value === "yaml") {
    tryParseYamlContent();
  }
});
</script>

<template>
  <section class="config-view">
    <aside class="file-list">
      <button
        v-for="item in files"
        :key="item.filename"
        type="button"
        class="file-item"
        :data-active="item.filename === selectedFile"
        @click="selectedFile = item.filename"
      >
        {{ item.filename }}
      </button>
    </aside>

    <main class="editor-panel">
      <header class="panel-header">
        <div>
          <p class="file-name">{{ selectedFile }}</p>
          <p class="file-hint">Config Editor</p>
        </div>
        <n-button type="primary" :loading="loading" @click="saveCurrentFile">
          保存
        </n-button>
      </header>

      <n-tabs v-model:value="activeTab" type="line" animated>
        <n-tab-pane name="form" tab="📋 表单">
          <n-card v-if="selectedFile === 'models.yaml'" title="models.yaml">
            <div class="section">
              <h4>模型列表</h4>
              <div class="row-grid row-header">
                <span>Model Name</span>
                <span>Provider</span>
                <span>LiteLLM Model</span>
                <span>Fallback</span>
                <span>Tool Calling</span>
                <span>Context Window</span>
              </div>
              <div
                v-for="model in models"
                :key="model.name"
                class="row-grid"
              >
                <n-input :value="model.name" disabled />
                <n-input
                  :value="String(model.provider ?? '')"
                  placeholder="provider"
                  @update:value="(val) => updateModelField(model.name, 'provider', val)"
                />
                <n-input
                  :value="String(model.litellm_model ?? '')"
                  placeholder="litellm_model"
                  @update:value="(val) => updateModelField(model.name, 'litellm_model', val)"
                />
                <n-input
                  :value="String(model.fallback ?? '')"
                  placeholder="fallback"
                  @update:value="(val) => updateModelField(model.name, 'fallback', val)"
                />
                <n-checkbox
                  :checked="model.supports_tool_calling"
                  @update:checked="(checked) => updateModelToolCalling(model.name, checked)"
                />
                <n-input-number
                  :value="model.context_window"
                  placeholder="context_window"
                  @update:value="(val) => updateModelContextWindow(model.name, val)"
                />
              </div>
            </div>
            <div class="section">
              <h4>task_routing</h4>
              <div
                v-for="item in taskRouting"
                :key="item.task"
                class="route-row"
              >
                <span>{{ item.task }}</span>
                <n-input
                  :value="item.model"
                  placeholder="模型名"
                  @update:value="(val) => updateTaskRouting(item.task, val)"
                />
              </div>
            </div>
          </n-card>

          <n-card v-else-if="selectedFile === 'skills.yaml'" title="skills.yaml">
            <NSpace vertical>
              <div
                v-for="item in skills"
                :key="item.name"
                class="skill-switch"
              >
                <span>{{ item.name }}</span>
                <n-switch
                  :value="item.enabled"
                  @update:value="(val) => updateSkillEnabled(item.name, val)"
                />
              </div>
            </NSpace>
          </n-card>

          <n-card v-else-if="selectedFile === 'security.yaml'" title="security.yaml">
            <div class="breaker-grid">
              <label>tool_level_max_failures</label>
              <n-input-number
                :value="Number((parsedConfig.circuit_breaker as any)?.tool_level_max_failures ?? 3)"
                @update:value="(val) => updateBreakerNumber('tool_level_max_failures', val)"
              />
              <label>session_level_max_failures</label>
              <n-input-number
                :value="Number((parsedConfig.circuit_breaker as any)?.session_level_max_failures ?? 5)"
                @update:value="(val) => updateBreakerNumber('session_level_max_failures', val)"
              />
              <label>cooldown_seconds</label>
              <n-input-number
                :value="Number((parsedConfig.circuit_breaker as any)?.cooldown_seconds ?? 120)"
                @update:value="(val) => updateBreakerNumber('cooldown_seconds', val)"
              />
            </div>

            <div class="section">
              <h4>白名单规则</h4>
              <div class="rule-header">
                <span>Path</span>
                <span>Permissions</span>
                <span>操作</span>
              </div>
              <div
                v-for="(rule, index) in whitelistRules"
                :key="`${index}-${rule.path}`"
                class="rule-row"
              >
                <n-input
                  :value="rule.path"
                  placeholder="/some/path"
                  @update:value="(val) => updateWhitelistRulePath(index, val)"
                />
                <n-checkbox-group
                  :value="rule.permissions"
                  @update:value="(vals) => updateWhitelistRulePermissions(index, vals)"
                >
                  <NSpace>
                    <n-checkbox
                      v-for="permission in whitelistPermissionOptions"
                      :key="permission"
                      :value="permission"
                      :label="permission"
                    />
                  </NSpace>
                </n-checkbox-group>
                <n-button size="small" type="error" secondary @click="removeWhitelistRule(index)">
                  删除
                </n-button>
              </div>
              <n-button size="small" tertiary type="primary" @click="addWhitelistRule">
                + Add Rule
              </n-button>
            </div>
          </n-card>

          <n-card v-else>
            此配置暂无表单视图，请切换到 YAML 编辑。
          </n-card>
        </n-tab-pane>

        <n-tab-pane name="yaml" tab="<> YAML">
          <MonacoEditor
            v-model="yamlContent"
            language="yaml"
            height="520px"
          />
        </n-tab-pane>
      </n-tabs>
    </main>
  </section>
</template>

<style scoped>
.config-view {
  display: grid;
  gap: 12px;
  grid-template-columns: 220px 1fr;
  height: 100%;
  min-height: 0;
}

.file-list {
  background: color-mix(in srgb, var(--surface) 88%, transparent);
  border: 1px solid var(--panel-edge);
  border-radius: 12px;
  display: grid;
  gap: 8px;
  max-height: 100%;
  overflow: auto;
  padding: 10px;
}

.file-item {
  background: transparent;
  border: 1px solid var(--panel-edge);
  border-radius: 8px;
  color: var(--text);
  cursor: pointer;
  padding: 8px;
  text-align: left;
}

.file-item[data-active="true"] {
  border-color: color-mix(in srgb, var(--brand) 60%, var(--panel-edge));
}

.editor-panel {
  min-height: 0;
  overflow: auto;
}

.panel-header {
  align-items: center;
  display: flex;
  justify-content: space-between;
  margin-bottom: 10px;
}

.file-name {
  font-size: 16px;
  font-weight: 700;
  margin: 0;
}

.file-hint {
  color: var(--muted);
  font-size: 12px;
  margin: 2px 0 0;
}

.section {
  margin-top: 12px;
}

.row-grid {
  align-items: center;
  display: grid;
  gap: 8px;
  grid-template-columns: 1.1fr 1fr 1.6fr 1fr 130px 160px;
  margin-bottom: 8px;
}

.row-header {
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 10px;
}

.route-row {
  align-items: center;
  display: grid;
  gap: 8px;
  grid-template-columns: 180px 1fr;
  margin-bottom: 8px;
}

.skill-switch {
  align-items: center;
  display: flex;
  justify-content: space-between;
}

.breaker-grid {
  align-items: center;
  display: grid;
  gap: 8px;
  grid-template-columns: 220px 1fr;
}

.rule-header {
  color: var(--muted);
  display: grid;
  font-size: 12px;
  font-weight: 700;
  gap: 8px;
  grid-template-columns: 1.4fr 1.2fr auto;
  margin-bottom: 8px;
}

.rule-row {
  align-items: center;
  display: grid;
  gap: 8px;
  grid-template-columns: 1.4fr 1.2fr auto;
  margin-bottom: 8px;
}

@media (max-width: 1023px) {
  .config-view {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1fr;
  }

  .row-grid {
    grid-template-columns: 1fr;
  }

  .row-header {
    display: none;
  }

  .rule-header {
    display: none;
  }

  .rule-row {
    grid-template-columns: 1fr;
  }
}
</style>
