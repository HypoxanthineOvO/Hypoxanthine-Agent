import MarkdownIt from "markdown-it";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdownLang from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import taskListsPlugin from "markdown-it-task-lists";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

import "highlight.js/styles/github-dark.css";

hljs.registerLanguage("bash", bash);
hljs.registerLanguage("css", css);
hljs.registerLanguage("html", xml);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("js", javascript);
hljs.registerLanguage("json", json);
hljs.registerLanguage("markdown", markdownLang);
hljs.registerLanguage("md", markdownLang);
hljs.registerLanguage("python", python);
hljs.registerLanguage("py", python);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("ts", typescript);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("yaml", yaml);
hljs.registerLanguage("yml", yaml);

function escapeHtml(raw: string): string {
  return raw
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

function renderCodeWithLineNumbers(codeHtml: string): string {
  const lines = codeHtml.split("\n");
  const normalizedLines = lines.length > 0 ? lines : [""];
  return normalizedLines
    .map((line, index) => {
      const content = line.length > 0 ? line : "&nbsp;";
      return `<span class="code-line"><span class="line-number">${index + 1}</span><span class="line-text">${content}</span></span>`;
    })
    .join("");
}

function createMathPlaceholder(expression: string, displayMode: boolean): string {
  return `<span class="katex-placeholder" data-katex-display="${displayMode ? "true" : "false"}" data-katex-source="${encodeURIComponent(expression)}">${escapeHtml(expression)}</span>`;
}

function mathPlugin(md: MarkdownIt): void {
  md.inline.ruler.after("escape", "math_inline", (state, silent) => {
    if (state.src[state.pos] !== "$" || state.src[state.pos + 1] === "$") {
      return false;
    }

    let match = state.pos + 1;
    while ((match = state.src.indexOf("$", match)) !== -1) {
      if (state.src[match - 1] !== "\\") {
        break;
      }
      match += 1;
    }

    if (match === -1 || match === state.pos + 1) {
      return false;
    }

    if (!silent) {
      const token = state.push("math_inline", "math", 0);
      token.content = state.src.slice(state.pos + 1, match);
    }
    state.pos = match + 1;
    return true;
  });

  md.block.ruler.after("blockquote", "math_block", (state, start, end, silent) => {
    const startPos = (state.bMarks[start] ?? 0) + (state.tShift[start] ?? 0);
    const maxPos = state.eMarks[start] ?? startPos;
    if (state.src.slice(startPos, startPos + 2) !== "$$") {
      return false;
    }

    let next = start;
    let content = state.src.slice(startPos + 2, maxPos);

    if (content.trimEnd().endsWith("$$")) {
      content = content.replace(/\$\$\s*$/, "");
      if (!silent) {
        const token = state.push("math_block", "math", 0);
        token.block = true;
        token.content = content.trim();
        token.map = [start, start + 1];
      }
      state.line = start + 1;
      return true;
    }

    while (++next < end) {
      const nextStart = (state.bMarks[next] ?? 0) + (state.tShift[next] ?? 0);
      const nextMax = state.eMarks[next] ?? nextStart;
      const line = state.src.slice(nextStart, nextMax);
      if (line.trimEnd().endsWith("$$")) {
        content += `\n${line.replace(/\$\$\s*$/, "")}`;
        if (!silent) {
          const token = state.push("math_block", "math", 0);
          token.block = true;
          token.content = content.trim();
          token.map = [start, next + 1];
        }
        state.line = next + 1;
        return true;
      }
      content += `\n${line}`;
    }

    return false;
  });

  md.renderer.rules.math_inline = (tokens, index) =>
    createMathPlaceholder(tokens[index]?.content ?? "", false);
  md.renderer.rules.math_block = (tokens, index) =>
    `<div class="katex-block">${createMathPlaceholder(tokens[index]?.content ?? "", true)}</div>`;
}

const markdown: MarkdownIt = new MarkdownIt({
  breaks: true,
  highlight(code: string, language: string): string {
    const normalizedLang = typeof language === "string" ? language.trim() : "";
    const dataCode = escapeHtml(code);
    const languageLabel = normalizedLang || "text";
    const header = `<div class="code-header"><span class="code-lang">${escapeHtml(languageLabel)}</span><button class="copy-btn" type="button" data-code="${dataCode}">复制</button></div>`;
    if (normalizedLang && hljs.getLanguage(normalizedLang)) {
      const highlighted = hljs.highlight(code, {
        language: normalizedLang,
        ignoreIllegals: true,
      }).value;
      return `<pre class="hljs code-block-wrapper">${header}<code class="language-${normalizedLang}">${renderCodeWithLineNumbers(highlighted)}</code></pre>`;
    }

    const languageClass = normalizedLang ? ` class="language-${normalizedLang}"` : "";
    return `<pre class="hljs code-block-wrapper">${header}<code${languageClass}>${renderCodeWithLineNumbers(dataCode)}</code></pre>`;
  },
  html: false,
  linkify: true,
})
  .use(taskListsPlugin as unknown as (md: MarkdownIt) => void)
  .use(mathPlugin);

export interface RenderMarkdownOptions {
  cacheKey?: string;
  version?: number | string;
  streaming?: boolean;
}

interface MarkdownCacheEntry {
  html: string;
  source: string;
  usedAt: number;
}

const MARKDOWN_RENDER_CACHE_LIMIT = 160;
const markdownRenderCache = new Map<string, MarkdownCacheEntry>();
let markdownCacheHits = 0;
let markdownCacheMisses = 0;

function markdownCacheId(source: string, options: RenderMarkdownOptions): string | null {
  const key = options.cacheKey?.trim();
  if (!key) {
    return null;
  }
  const version = options.version ?? source.length;
  return `${key}:${String(version)}:${options.streaming === true ? "streaming" : "final"}`;
}

function evictOldMarkdownCacheEntries(): void {
  while (markdownRenderCache.size > MARKDOWN_RENDER_CACHE_LIMIT) {
    let oldestKey: string | null = null;
    let oldestUsedAt = Number.POSITIVE_INFINITY;
    for (const [key, entry] of markdownRenderCache.entries()) {
      if (entry.usedAt < oldestUsedAt) {
        oldestKey = key;
        oldestUsedAt = entry.usedAt;
      }
    }
    if (oldestKey === null) {
      return;
    }
    markdownRenderCache.delete(oldestKey);
  }
}

export function clearMarkdownRenderCache(): void {
  markdownRenderCache.clear();
  markdownCacheHits = 0;
  markdownCacheMisses = 0;
}

export function getMarkdownRenderCacheStats(): {
  entries: number;
  hits: number;
  misses: number;
} {
  return {
    entries: markdownRenderCache.size,
    hits: markdownCacheHits,
    misses: markdownCacheMisses,
  };
}

export function renderMarkdown(
  source: string,
  options: RenderMarkdownOptions = {},
): string {
  const cacheId = markdownCacheId(source, options);
  if (cacheId) {
    const cached = markdownRenderCache.get(cacheId);
    if (cached && cached.source === source) {
      cached.usedAt = Date.now();
      markdownCacheHits += 1;
      return cached.html;
    }
  }

  markdownCacheMisses += cacheId ? 1 : 0;
  const html = markdown.render(source);
  if (cacheId) {
    markdownRenderCache.set(cacheId, {
      html,
      source,
      usedAt: Date.now(),
    });
    evictOldMarkdownCacheEntries();
  }
  return html;
}

function hasUnclosedCodeFence(source: string): boolean {
  const fenceLines = source
    .split(/\r?\n/)
    .filter((line) => /^\s*(```|~~~)/.test(line));
  return fenceLines.length % 2 === 1;
}

function hasUnclosedDisplayMath(source: string): boolean {
  const markers = source.match(/(^|\n)\s*\$\$/g) ?? [];
  return markers.length % 2 === 1;
}

export function shouldRenderEnhancedMarkdown(
  source: string,
  options: Pick<RenderMarkdownOptions, "streaming"> = {},
): boolean {
  if (options.streaming !== true) {
    return true;
  }
  return !hasUnclosedCodeFence(source) && !hasUnclosedDisplayMath(source);
}

let mermaidLoader: Promise<typeof import("mermaid")> | null = null;
let katexLoader: Promise<typeof import("katex")> | null = null;

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

async function loadKatex(): Promise<typeof import("katex")> {
  if (!katexLoader) {
    katexLoader = Promise.all([
      import("katex"),
      import("katex/dist/katex.min.css").catch(() => undefined),
    ]).then(([mod]) => mod);
  }
  return katexLoader;
}

export async function renderMathIn(container: HTMLElement): Promise<void> {
  const mathNodes = container.querySelectorAll<HTMLElement>("[data-katex-source]");
  if (mathNodes.length === 0) {
    return;
  }

  const katex = await loadKatex();
  for (const node of mathNodes) {
    const source = node.dataset.katexSource;
    if (!source) {
      continue;
    }

    try {
      node.outerHTML = katex.renderToString(decodeURIComponent(source), {
        displayMode: node.dataset.katexDisplay === "true",
        throwOnError: false,
      });
    } catch {
      node.textContent = decodeURIComponent(source);
    }
  }
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
