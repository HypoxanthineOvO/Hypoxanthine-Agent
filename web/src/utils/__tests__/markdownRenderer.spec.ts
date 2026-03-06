import { describe, expect, it } from "vitest";

import { renderMarkdown } from "../markdownRenderer";

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

  it("renders katex", () => {
    const html = renderMarkdown("$x^2 + y^2 = z^2$");
    expect(html).toContain("katex");
  });

  it("keeps mermaid code block marker for post-render", () => {
    const html = renderMarkdown("```mermaid\nflowchart LR\nA-->B\n```\n");
    expect(html).toContain("language-mermaid");
  });
});
