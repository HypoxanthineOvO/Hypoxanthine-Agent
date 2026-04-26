import { describe, expect, it } from "vitest";

import {
  clearMarkdownRenderCache,
  getMarkdownRenderCacheStats,
  renderMarkdown,
  renderMathIn,
  shouldRenderEnhancedMarkdown,
} from "../markdownRenderer";

describe("markdownRenderer", () => {
  it("renders gfm table", () => {
    const html = renderMarkdown("| a | b |\n|---|---|\n| 1 | 2 |\n");
    expect(html).toContain("<table>");
    expect(html).toContain("<td>1</td>");
  });

  it("renders task list", () => {
    const html = renderMarkdown("- [x] done\n- [ ] todo\n");
    expect(html).toContain('type="checkbox"');
  });

  it("marks katex for lazy post-render", () => {
    const html = renderMarkdown("$x^2 + y^2 = z^2$");
    expect(html).toContain("data-katex-source");
  });

  it("renders katex placeholders after lazy load", async () => {
    const container = document.createElement("div");
    container.innerHTML = renderMarkdown("$x^2 + y^2 = z^2$");

    await renderMathIn(container);

    expect(container.innerHTML).toContain("katex");
  });

  it("keeps mermaid code block marker for post-render", () => {
    const html = renderMarkdown("```mermaid\nflowchart LR\nA-->B\n```\n");
    expect(html).toContain("language-mermaid");
  });

  it("adds code header with language label and copy button", () => {
    const html = renderMarkdown("```python\nprint('ok')\n```\n");
    expect(html).toContain('class="code-header"');
    expect(html).toContain('class="code-lang"');
    expect(html).toContain("python");
    expect(html).toContain('class="copy-btn"');
    expect(html).toContain("data-code=");
  });

  it("caches rendered markdown by message id and version", () => {
    clearMarkdownRenderCache();

    const first = renderMarkdown("**ok**", { cacheKey: "message-1", version: 1 });
    const second = renderMarkdown("**ok**", { cacheKey: "message-1", version: 1 });
    const third = renderMarkdown("**changed**", { cacheKey: "message-1", version: 2 });
    const stats = getMarkdownRenderCacheStats();

    expect(second).toBe(first);
    expect(third).not.toBe(first);
    expect(stats.hits).toBe(1);
    expect(stats.entries).toBe(2);
  });

  it("defers enhanced math and mermaid rendering while streaming incomplete blocks", () => {
    expect(
      shouldRenderEnhancedMarkdown("```mermaid\nflowchart LR\nA-->B\n", {
        streaming: true,
      }),
    ).toBe(false);
    expect(
      shouldRenderEnhancedMarkdown("$$\nx^2 + y^2 = z^2\n", {
        streaming: true,
      }),
    ).toBe(false);
    expect(
      shouldRenderEnhancedMarkdown("```mermaid\nflowchart LR\nA-->B\n```\n", {
        streaming: true,
      }),
    ).toBe(true);
    expect(
      shouldRenderEnhancedMarkdown("```mermaid\nflowchart LR\nA-->B\n", {
        streaming: false,
      }),
    ).toBe(true);
  });
});
