import MarkdownIt from "markdown-it";
import hljs from "highlight.js";
import katexPlugin from "@traptitech/markdown-it-katex";
import taskListsPlugin from "markdown-it-task-lists";

import "katex/dist/katex.min.css";
import "highlight.js/styles/github-dark.css";

function escapeHtml(raw: string): string {
  return raw
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

const markdown: MarkdownIt = new MarkdownIt({
  breaks: true,
  highlight(code: string, language: string): string {
    const normalizedLang = typeof language === "string" ? language.trim() : "";
    if (normalizedLang && hljs.getLanguage(normalizedLang)) {
      const highlighted = hljs.highlight(code, {
        language: normalizedLang,
        ignoreIllegals: true,
      }).value;
      return `<pre class="hljs"><code class="language-${normalizedLang}">${highlighted}</code></pre>`;
    }

    const escaped = escapeHtml(code);
    const languageClass = normalizedLang ? ` class="language-${normalizedLang}"` : "";
    return `<pre class="hljs"><code${languageClass}>${escaped}</code></pre>`;
  },
  html: false,
  linkify: true,
})
  .use(taskListsPlugin as unknown as (md: MarkdownIt) => void)
  .use(katexPlugin as unknown as (md: MarkdownIt) => void);

export function renderMarkdown(source: string): string {
  return markdown.render(source);
}

let mermaidLoader: Promise<typeof import("mermaid")> | null = null;

async function loadMermaid(): Promise<typeof import("mermaid")> {
  if (mermaidLoader) {
    return mermaidLoader;
  }

  mermaidLoader = import("mermaid");
  const mod = await mermaidLoader;
  const instance = mod.default;
  instance.initialize({
    startOnLoad: false,
    securityLevel: "strict",
  });
  return mod;
}

export async function renderMermaidIn(container: HTMLElement): Promise<void> {
  const codeBlocks = container.querySelectorAll("pre code.language-mermaid");
  if (codeBlocks.length === 0) {
    return;
  }

  const mod = await loadMermaid();
  const mermaid = mod.default;

  for (const codeBlock of codeBlocks) {
    const chartSource = codeBlock.textContent ?? "";
    const parentPre = codeBlock.closest("pre");
    if (!parentPre || chartSource.trim().length === 0) {
      continue;
    }

    const target = document.createElement("div");
    target.className = "mermaid-render";

    try {
      const renderId = `mermaid-${Math.random().toString(36).slice(2)}`;
      const rendered = await mermaid.render(renderId, chartSource);
      target.innerHTML = rendered.svg;
      parentPre.replaceWith(target);
    } catch {
      target.textContent = "Mermaid 渲染失败";
      parentPre.replaceWith(target);
    }
  }
}
