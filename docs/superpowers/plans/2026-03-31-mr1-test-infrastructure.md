# MR.1 Test Infrastructure Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate duplicated test utilities, delete CSS source-scan assertions, and add coverage config — establishing a clean test foundation for MR.5.

**Architecture:** Create a single shared `src/test/utils.ts` exporting `MockWebSocket`, `installMockWebSocket`, `uninstallMockWebSocket`, `flushUi`, and `createMockMessage`. Update all five views/composables spec files to import from there. Delete 11 CSS/source-scan tests. Add v8 coverage config to `vitest.config.ts`. Fix `innerWidth` leak in `App.spec.ts`.

**Tech Stack:** Vitest 4, Vue Test Utils, TypeScript, jsdom, @vitest/coverage-v8

---

## File Map

| Action | File |
|--------|------|
| Create | `web/src/test/utils.ts` |
| Modify | `web/src/composables/__tests__/useChatSocket.spec.ts` |
| Modify | `web/src/views/__tests__/ChatView.spec.ts` |
| Modify | `web/src/views/__tests__/DashboardView.spec.ts` |
| Modify | `web/src/views/__tests__/ConfigView.spec.ts` |
| Modify | `web/src/views/__tests__/MemoryView.spec.ts` |
| Modify | `web/src/__tests__/App.spec.ts` |
| Modify | `web/vitest.config.ts` |

---

### Task 1: Create `src/test/utils.ts`

**Files:**
- Create: `web/src/test/utils.ts`

- [ ] **Step 1: Write `web/src/test/utils.ts`**

```typescript
import { nextTick } from "vue";
import type { Message } from "@/types/message";

// ---------------------------------------------------------------------------
// MockWebSocket
// ---------------------------------------------------------------------------

export class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  readonly url: string;
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  sent: string[] = [];
  readyState = MockWebSocket.CONNECTING;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({} as CloseEvent);
  }

  emitOpen(): void {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  emitMessage(data: string): void {
    this.onmessage?.({ data } as MessageEvent);
  }

  emitClose(): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({} as CloseEvent);
  }
}

export function installMockWebSocket(): void {
  MockWebSocket.instances = [];
  globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
}

export function uninstallMockWebSocket(): void {
  // globalThis.WebSocket is restored by vi.unstubAllGlobals() in afterEach;
  // this resets the instance list so tests don't bleed state.
  MockWebSocket.instances = [];
}

// ---------------------------------------------------------------------------
// flushUi
// ---------------------------------------------------------------------------

export async function flushUi(): Promise<void> {
  await Promise.resolve();
  await nextTick();
}

// ---------------------------------------------------------------------------
// createMockMessage
// ---------------------------------------------------------------------------

export function createMockMessage(
  overrides: Partial<Message> & { sender: string; session_id: string },
): Message {
  return {
    text: null,
    ...overrides,
  };
}
```

- [ ] **Step 2: Verify the file compiles**

Run: `cd /home/heyx/Hypo-Agent/web && npx tsc --noEmit --skipLibCheck 2>&1 | head -30`

Expected: no errors about `src/test/utils.ts`

---

### Task 2: Update `useChatSocket.spec.ts`

**Files:**
- Modify: `web/src/composables/__tests__/useChatSocket.spec.ts:1-53`

Replace the local `MockWebSocket` class and setup with imports from `@/test/utils`.

- [ ] **Step 1: Replace the top of the file**

Remove lines 1–53 (the import block, the full `MockWebSocket` class definition, and the `beforeEach` that resets `instances` and assigns `globalThis.WebSocket`).

Replace with:

```typescript
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";

import { MockWebSocket, installMockWebSocket } from "@/test/utils";
import { useChatSocket } from "../useChatSocket";

beforeEach(() => {
  installMockWebSocket();
});
```

- [ ] **Step 2: Run the test file in isolation**

Run: `cd /home/heyx/Hypo-Agent/web && npx vitest run src/composables/__tests__/useChatSocket.spec.ts 2>&1 | tail -20`

Expected: `7 passed`

- [ ] **Step 3: Commit**

```bash
cd /home/heyx/Hypo-Agent/web && git add src/test/utils.ts src/composables/__tests__/useChatSocket.spec.ts
git commit -m "MR.1: extract shared MockWebSocket and flushUi to test/utils, update useChatSocket.spec"
```

---

### Task 3: Update `ChatView.spec.ts` — extract utils + delete CSS tests

**Files:**
- Modify: `web/src/views/__tests__/ChatView.spec.ts`

Three changes in one edit:
1. Remove the local `MockWebSocket` class (lines 11–45)
2. Remove the local `flushUi` function (lines 63–66)
3. Delete the 3 CSS assertion tests: `"keeps the chat layout on a pure flex column chain"`, `"fills the full width of the parent content pane"`, `"does not use positional anchoring for the composer chain"`

- [ ] **Step 1: Update imports at the top of the file**

Replace:
```typescript
/// <reference types="node" />

import { mount } from "@vue/test-utils";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ChatView from "../ChatView.vue";

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(): void {}

  close(): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({} as CloseEvent);
  }

  emitOpen(): void {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  emitMessage(data: string): void {
    this.onmessage?.({ data } as MessageEvent);
  }
}

beforeEach(() => {
  MockWebSocket.instances = [];
  globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function flushUi(): Promise<void> {
  await Promise.resolve();
  await nextTick();
}

const chatViewSource = readFileSync(
  resolve(process.cwd(), "src/views/ChatView.vue"),
  "utf8",
);
const chatPageBlock = chatViewSource.match(/\.chat-page\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
```

With:
```typescript
import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MockWebSocket, flushUi, installMockWebSocket } from "@/test/utils";
import ChatView from "../ChatView.vue";

beforeEach(() => {
  installMockWebSocket();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});
```

- [ ] **Step 2: Delete the 3 CSS test cases**

Delete this entire `it` block ("keeps the chat layout on a pure flex column chain"):
```typescript
  it("keeps the chat layout on a pure flex column chain", () => {
    expect(chatViewSource).toContain('class="chat-page"');
    // ... 9 expects ...
  });
```

Delete this entire `it` block ("fills the full width of the parent content pane"):
```typescript
  it("fills the full width of the parent content pane", () => {
    expect(chatPageBlock).toMatch(/width:\s*100%;/);
  });
```

Delete this entire `it` block ("does not use positional anchoring for the composer chain"):
```typescript
  it("does not use positional anchoring for the composer chain", () => {
    const inputAreaBlock = chatViewSource.match(/\.input-area\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
    // ... 5 expects ...
  });
```

- [ ] **Step 3: Run the test file in isolation**

Run: `cd /home/heyx/Hypo-Agent/web && npx vitest run src/views/__tests__/ChatView.spec.ts 2>&1 | tail -20`

Expected: all remaining tests pass (approximately 13 tests)

- [ ] **Step 4: Commit**

```bash
cd /home/heyx/Hypo-Agent/web && git add src/views/__tests__/ChatView.spec.ts
git commit -m "MR.1: ChatView.spec — use shared utils, delete 3 CSS assertions"
```

---

### Task 4: Update `App.spec.ts` — extract utils + delete CSS tests + fix innerWidth leak

**Files:**
- Modify: `web/src/__tests__/App.spec.ts`

Four changes:
1. Remove local `MockWebSocket` class
2. Remove local `flushUi` function
3. Delete 3 CSS/source-scan tests
4. Add `afterEach` restoration of `innerWidth`

- [ ] **Step 1: Replace the entire file header (through the `flushUi` function)**

Replace:
```typescript
/// <reference types="node" />

import { mount } from "@vue/test-utils";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App.vue";

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;

  constructor(url: string) {
    this.url = url;
  }

  send(): void {}
  close(): void {}
}

beforeEach(() => {
  globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function flushUi(): Promise<void> {
  await Promise.resolve();
  await nextTick();
}

const appSource = readFileSync(resolve(process.cwd(), "src/App.vue"), "utf8");
const globalStyleSource = readFileSync(resolve(process.cwd(), "src/style.css"), "utf8");
const mainBodyBlock = appSource.match(/\.main-body\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
```

With:
```typescript
import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { flushUi, installMockWebSocket } from "@/test/utils";
import App from "../App.vue";

let originalInnerWidth: number;

beforeEach(() => {
  installMockWebSocket();
  originalInnerWidth = window.innerWidth;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    }),
  );
});

afterEach(() => {
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: originalInnerWidth,
  });
  vi.unstubAllGlobals();
});
```

- [ ] **Step 2: Delete the 3 CSS/source-scan test cases**

Delete `it("propagates the full-height chain into the chat surface", ...)` — the block with 9 `expect` calls on `globalStyleSource` and `appSource`.

Delete `it("keeps the main content area stretched to the full remaining width", ...)` — the block with 3 `expect` calls on `mainBodyBlock`.

Delete `it("applies card radius and elevation through global Naive UI theme overrides", ...)` — the block with 3 `expect` calls on `appSource`.

- [ ] **Step 3: Run the test file in isolation**

Run: `cd /home/heyx/Hypo-Agent/web && npx vitest run src/__tests__/App.spec.ts 2>&1 | tail -20`

Expected: 2 tests pass (`updates the browser tab title`, `shows a reachable mobile navigation bar`)

- [ ] **Step 4: Commit**

```bash
cd /home/heyx/Hypo-Agent/web && git add src/__tests__/App.spec.ts
git commit -m "MR.1: App.spec — use shared utils, delete 3 CSS assertions, fix innerWidth leak"
```

---

### Task 5: Update `DashboardView.spec.ts` — extract utils + replace source-scan test + delete 2 CSS tests

**Files:**
- Modify: `web/src/views/__tests__/DashboardView.spec.ts`

Three changes:
1. Remove local `flushUi` function and `readFileSync` imports/variables
2. Delete `it("fills the full width of the content area", ...)` (CSS assertion)
3. Delete `it("stretches top-level cards so each two-column row renders equal card heights", ...)` (5 CSS assertions)
4. Replace `it("renders WebUI, QQ and 微信 through the same shared channel card component\", ...)` with a behavior test that mounts DashboardView and checks for rendered elements.

- [ ] **Step 1: Remove `readFileSync` imports and source-scan variable declarations**

Remove these lines from the top of the file:
```typescript
/// <reference types=\"node\" />
```
```typescript
import { readFileSync } from \"node:fs\";
import { resolve } from \"node:path\";
```

Also remove these 5 variable declarations (after the `vi.mock` block, before `import DashboardView`):
```typescript
const dashboardSource = readFileSync(
  resolve(process.cwd(), \"src/views/DashboardView.vue\"),
  \"utf8\",
);
const dashboardRootBlock =
  dashboardSource.match(/\\.dashboard-view\\s*\\{([\\s\\S]*?)\
\\}/)?.[1] ?? \"\";
const dashboardGridItemBlock =
  dashboardSource.match(/\\.dashboard-grid\\s*:deep\\(\\.n-grid-item\\)\\s*\\{([\\s\\S]*?)\
\\}/)?.[1] ?? \"\";
const dashboardCardBlock =
  dashboardSource.match(/\\.dashboard-card\\s*\\{([\\s\\S]*?)\
\\}/)?.[1] ?? \"\";
```

- [ ] **Step 2: Add import for shared utils**

Add after the existing vitest import line:
```typescript
import { flushUi } from \"@/test/utils\";
```

Remove the local `flushUi` function:
```typescript
async function flushUi(): Promise<void> {
  await Promise.resolve();
  await nextTick();
}
```

Also remove the `nextTick` import from `\"vue\"` since `flushUi` no longer lives here.

- [ ] **Step 3: Delete the 2 CSS test cases**

Delete:
```typescript
  it(\"fills the full width of the content area\", () => {
    expect(dashboardRootBlock).toMatch(/width:\\s*100%;/);
  });
```

Delete:
```typescript
  it(\"stretches top-level cards so each two-column row renders equal card heights\", () => {
    expect(dashboardSource).toContain('class=\"dashboard-grid\"');
    expect(dashboardSource).toContain('class=\"dashboard-card');
    expect(dashboardGridItemBlock).toMatch(/display:\\s*flex;/);
    expect(dashboardCardBlock).toMatch(/height:\\s*100%;/);
    expect(dashboardCardBlock).toMatch(/display:\\s*flex;/);
    expect(dashboardCardBlock).toMatch(/flex-direction:\\s*column;/);
  });
```

- [ ] **Step 4: Replace the source-scan test with a behavior test**

Replace:
```typescript
  it(\"renders WebUI, QQ and 微信 through the same shared channel card component\", () => {
    expect(dashboardSource).toContain('import ChannelStatusCard from \"../components/dashboard/ChannelStatusCard.vue\";');
    expect(dashboardSource).not.toContain(\"WeixinStatusCard\");
    expect(dashboardSource).toContain(':title=\"channelCardMap.webui.name\"');
    expect(dashboardSource).toContain(':title=\"channelCardMap.qqBot.name\"');
    expect(dashboardSource).toContain(':title=\"channelCardMap.weixin.name\"');
  });
```

With:
```typescript
  it(\"renders WebUI, QQ and 微信 channel cards in the DOM\", async () => {
    mockDashboardFetch(
      [{ date: \"2026-03-06\", model: \"Gemini3Pro\", total_tokens: 100 }],
      [{ date: \"2026-03-06\", p50_ms: 50, p95_ms: 80, p99_ms: 120 }],
    );

    const wrapper = mount(DashboardView, {
      props: {
        token: \"test-token\",
        apiBase: \"http://localhost:8000/api\",
      },
    });

    await flushUi();
    await flushUi();
    await flushUi();

    // All three channel types are rendered as named cards in the DOM
    expect(wrapper.text()).toContain(\"WebUI\");
    expect(wrapper.text()).toContain(\"QQ Bot\");
    expect(wrapper.text()).toContain(\"微信\");
  });
```

- [ ] **Step 5: Run the test file in isolation**

Run: `cd /home/heyx/Hypo-Agent/web && npx vitest run src/views/__tests__/DashboardView.spec.ts 2>&1 | tail -20`

Expected: all tests pass (approximately 9 tests, down from 12)

- [ ] **Step 6: Commit**

```bash
cd /home/heyx/Hypo-Agent/web && git add src/views/__tests__/DashboardView.spec.ts
git commit -m \"MR.1: DashboardView.spec — use shared flushUi, delete 2 CSS assertions, replace source-scan test with behavior test\"
```

---

### Task 6: Update `ConfigView.spec.ts` — extract utils + delete 2 CSS tests

**Files:**
- Modify: `web/src/views/__tests__/ConfigView.spec.ts`

Three changes:
1. Remove `readFileSync` imports and source-scan variable declarations
2. Remove local `flushUi` function
3. Delete `it(\"fills the full width of the content area\", ...)` and `it(\"lays out skill cards in an equal-height responsive grid\", ...)`

- [ ] **Step 1: Remove `readFileSync` imports and source-scan variables**

Remove:
```typescript
/// <reference types=\"node\" />
```
```typescript
import { readFileSync } from \"node:fs\";
import { resolve } from \"node:path\";
```

Remove these variable declarations (lines 11–20):
```typescript
const configSource = readFileSync(
  resolve(process.cwd(), \"src/views/ConfigView.vue\"),
  \"utf8\",
);
const configFormSource = readFileSync(
  resolve(process.cwd(), \"src/components/ConfigFormRenderer.vue\"),
  \"utf8\",
);
const configRootBlock = configSource.match(/\\.config-page\\s*\\{([\\s\\S]*?)\
\\}/)?.[1] ?? \"\";
const skillsGridBlock = configFormSource.match(/\\.skills-grid\\s*\\{([\\s\\S]*?)\
\\}/)?.[1] ?? \"\";
```

- [ ] **Step 2: Add import for shared utils and remove local `flushUi`**

Add after the vitest import:
```typescript
import { flushUi } from \"@/test/utils\";
```

Remove the local:
```typescript
async function flushUi(): Promise<void> {
  await Promise.resolve();
  await nextTick();
}
```

Remove `nextTick` from the `\"vue\"` import (it's no longer used directly).

- [ ] **Step 3: Delete 2 CSS test cases**

Delete:
```typescript
  it(\"fills the full width of the content area\", () => {
    expect(configRootBlock).toMatch(/width:\\s*100%;/);
  });
```

Delete:
```typescript
  it(\"lays out skill cards in an equal-height responsive grid\", () => {
    expect(configFormSource).toContain('class=\"skills-grid\"');
    expect(configFormSource).toContain('class=\"skill-card\"');
    expect(skillsGridBlock).toMatch(
      /grid-template-columns:\\s*repeat\\(auto-fill,\\s*minmax\\(280px,\\s*1fr\\)\\);/,
    );
    expect(skillsGridBlock).toMatch(/align-items:\\s*stretch;/);
  });
```

- [ ] **Step 4: Run the test file in isolation**

Run: `cd /home/heyx/Hypo-Agent/web && npx vitest run src/views/__tests__/ConfigView.spec.ts 2>&1 | tail -20`

Expected: 3 tests pass (the 3 behavior tests)

- [ ] **Step 5: Commit**

```bash
cd /home/heyx/Hypo-Agent/web && git add src/views/__tests__/ConfigView.spec.ts
git commit -m \"MR.1: ConfigView.spec — use shared flushUi, delete 2 CSS assertions\"
```

---

### Task 7: Update `MemoryView.spec.ts` — extract utils + delete 1 CSS test

**Files:**
- Modify: `web/src/views/__tests__/MemoryView.spec.ts`

- [ ] **Step 1: Remove `readFileSync` imports and source-scan variables**

Remove:
```typescript
/// <reference types=\"node\" />
```
```typescript
import { readFileSync } from \"node:fs\";
import { resolve } from \"node:path\";
```

Remove these variable declarations (lines 11–15):
```typescript
const memorySource = readFileSync(
  resolve(process.cwd(), \"src/views/MemoryView.vue\"),
  \"utf8\",
);
const memoryRootBlock = memorySource.match(/\\.memory-view\\s*\\{([\\s\\S]*?)\
\\}/)?.[1] ?? \"\";
```

- [ ] **Step 2: Add import for shared utils and remove local `flushUi`**

Add after the vitest import:
```typescript
import { flushUi } from \"@/test/utils\";
```

Remove the local:
```typescript
async function flushUi(): Promise<void> {
  await Promise.resolve();
  await nextTick();
}
```

Remove `nextTick` from the `\"vue\"` import.

- [ ] **Step 3: Delete 1 CSS test case**

Delete:
```typescript
  it(\"fills the full width of the content area\", () => {
    expect(memoryRootBlock).toMatch(/width:\\s*100%;/);
  });
```

- [ ] **Step 4: Run the test file in isolation**

Run: `cd /home/heyx/Hypo-Agent/web && npx vitest run src/views/__tests__/MemoryView.spec.ts 2>&1 | tail -20`

Expected: 1 test passes (`loads l1 l2 l3 data with tokenized requests`)

- [ ] **Step 5: Commit**

```bash
cd /home/heyx/Hypo-Agent/web && git add src/views/__tests__/MemoryView.spec.ts
git commit -m \"MR.1: MemoryView.spec — use shared flushUi, delete 1 CSS assertion\"
```

---

### Task 8: Add coverage config to `vitest.config.ts`

**Files:**
- Modify: `web/vitest.config.ts`

- [ ] **Step 1: Update `vitest.config.ts`**

Replace the entire file with:
```typescript
import vue from \"@vitejs/plugin-vue\";
import { resolve } from \"node:path\";
import { defineConfig } from \"vitest/config\";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      \"@\": resolve(__dirname, \"src\"),
    },
  },
  test: {
    environment: \"jsdom\",
    globals: true,
    setupFiles: [\"./src/test/setup.ts\"],
    coverage: {
      provider: \"v8\",
      reporter: [\"text\", \"lcov\"],
      include: [\"src/**/*.{ts,vue}\"],
      exclude: [\"src/test/**\", \"src/**/*.spec.ts\"],
    },
  },
});
```

> **Note:** Check whether `vitest.config.ts` already has a `resolve.alias` for `@`. If it does, don't duplicate it. If not, add it as shown — the `@/test/utils` imports in spec files require this alias to resolve.

- [ ] **Step 2: Install coverage provider if not present**

Run: `cd /home/heyx/Hypo-Agent/web && npm list @vitest/coverage-v8 2>&1 | head -5`

If not installed: `npm install -D @vitest/coverage-v8`

- [ ] **Step 3: Verify coverage runs**

Run: `cd /home/heyx/Hypo-Agent/web && npx vitest run --coverage 2>&1 | tail -30`

Expected: coverage table printed, no errors

- [ ] **Step 4: Commit**

```bash
cd /home/heyx/Hypo-Agent/web && git add vitest.config.ts
git commit -m \"MR.1: add v8 coverage config and @ alias to vitest.config.ts\"
```

---

### Task 9: Full suite verification

**Files:** none

- [ ] **Step 1: Run the full test suite**

Run: `cd /home/heyx/Hypo-Agent/web && npm test 2>&1 | tail -30`

Expected output:
- All tests green
- Test count approximately 67 (78 − 11 deleted CSS tests)
- Zero failures

- [ ] **Step 2: Verify no `readFileSync` remains in spec files**

Run: `grep -r \"readFileSync\" /home/heyx/Hypo-Agent/web/src --include=\"*.spec.ts\"`

Expected: no output

- [ ] **Step 3: Verify `MockWebSocket` is defined only once**

Run: `grep -r \"class MockWebSocket\" /home/heyx/Hypo-Agent/web/src --include=\"*.ts\"`

Expected: only `src/test/utils.ts` appears

- [ ] **Step 4: Verify `flushUi` is defined only once**

Run: `grep -rn \"async function flushUi\" /home/heyx/Hypo-Agent/web/src --include=\"*.ts\"`

Expected: only `src/test/utils.ts` appears

- [ ] **Step 5: Final commit**

```bash
cd /home/heyx/Hypo-Agent/web && git add -A
git commit -m \"MR.1: test infrastructure cleanup complete\" --allow-empty
```

---

## Self-Review

### Spec Coverage

| Spec Requirement | Task |
|---|---|
| Create `src/test/utils.ts` with `MockWebSocket`, `installMockWebSocket`, `uninstallMockWebSocket`, `flushUi`, `createMockMessage` | Task 1 |
| Update `useChatSocket.spec.ts` to import from utils | Task 2 |
| Update `ChatView.spec.ts`, delete 3 CSS tests | Task 3 |
| Update `App.spec.ts`, delete 3 CSS tests, fix innerWidth leak | Task 4 |
| Update `DashboardView.spec.ts`, delete 2 CSS tests, replace source-scan test | Task 5 |
| Update `ConfigView.spec.ts`, delete 2 CSS tests | Task 6 |
| Update `MemoryView.spec.ts`, delete 1 CSS test | Task 7 |
| vitest coverage config | Task 8 |
| Acceptance criteria verification | Task 9 |

### Placeholder Scan
No TBD/TODO/placeholder language found.

### Type Consistency
- `MockWebSocket` defined in Task 1, imported identically in Tasks 2–4.
- `flushUi` defined in Task 1, imported identically in Tasks 3–7.
- `Message` type imported from `@/types/message` in `createMockMessage` — matches `web/src/types/message.ts`.
- `installMockWebSocket` used in Tasks 2, 3, 4 — defined in Task 1.
