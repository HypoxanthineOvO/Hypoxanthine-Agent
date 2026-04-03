import { describe, expect, it } from "vitest";

import { renderMarkdown, renderMathIn } from "../markdownRenderer";

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
});
