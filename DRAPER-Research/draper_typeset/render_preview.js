#!/usr/bin/env node
/* Render final.md to preview.html with KaTeX, mimicking Obsidian's renderer.
 * Usage: node render_preview.js
 */
const fs = require("fs");
const katex = require("katex");

let md = fs.readFileSync(process.env.MD||"final.md", "utf8");

// ---- extract fenced code blocks so markdown++ doesn't touch them ----
const fences = [];
md = md.replace(/```(\w*)\n([\s\S]*?)```/g, (m, lang, body) => {
  fences.push({ lang, body });
  return `\x00FENCE${fences.length - 1}\x00`;
});

// ---- display math $$ ... $$ ----
md = md.replace(/\$\$([\s\S]+?)\$\$/g, (m, src) => {
  try {
    return katex.renderToString(src.trim(), { displayMode: true, throwOnError: true, strict: false });
  } catch (e) {
    return `<pre class="err">KATEX ERROR: ${e.message}</pre>`;
  }
});

// ---- inline math $ ... $ ----
md = md.replace(/(?<!\$)\$(?!\$)((?:\\.|[^$\\])+?)\$(?!\$)/g, (m, src) => {
  try {
    return katex.renderToString(src, { displayMode: false, throwOnError: true, strict: false });
  } catch (e) {
    return `<span class="err">KATEX ERROR: ${e.message}</span>`;
  }
});

// ---- minimal markdown: headings, tables, lists, bold, code ----
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const lines = md.split("\n");
const out = [];
let i = 0;
let inList = false;
while (i < lines.length) {
  let l = lines[i];
  const fenceM = l.match(/^\x00FENCE(\d+)\x00$/);
  if (fenceM) {
    if (inList) { out.push("</ul>"); inList = false; }
    const f = fences[+fenceM[1]];
    out.push(`<pre class="code"><code>${esc(f.body)}</code></pre>`);
    i++; continue;
  }
  if (/^\|/.test(l) && i + 1 < lines.length && /^\|[\s:|-]+\|$/.test(lines[i + 1])) {
    if (inList) { out.push("</ul>"); inList = false; }
    const hdr = l.split("|").slice(1, -1).map(c => `<th>${c.trim()}</th>`).join("");
    i += 2;
    const rows = [];
    while (i < lines.length && /^\|/.test(lines[i])) {
      rows.push("<tr>" + lines[i].split("|").slice(1, -1).map(c => `<td>${c.trim()}</td>`).join("") + "</tr>");
      i++;
    }
    out.push(`<table><thead><tr>${hdr}</tr></thead><tbody>${rows.join("")}</tbody></table>`);
    continue;
  }
  let h = l.match(/^(#{1,6})\s+(.*)$/);
  if (h) {
    if (inList) { out.push("</ul>"); inList = false; }
    out.push(`<h${h[1].length}>${h[2]}</h${h[1].length}>`);
    i++; continue;
  }
  if (/^\s*([-*+]|\d+\.)\s+/.test(l)) {
    if (!inList) { out.push("<ul>"); inList = true; }
    out.push(`<li>${l.replace(/^\s*([-*+]|\d+\.)\s+/, "")}</li>`);
    i++; continue;
  }
  if (inList) { out.push("</ul>"); inList = false; }
  if (l.trim() === "") { out.push(""); i++; continue; }
  out.push(`<p>${l}</p>`);
  i++;
}
if (inList) out.push("</ul>");

let body = out.join("\n");
body = body.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
body = body.replace(/`([^`]+)`/g, "<code>$1</code>");
// markdown links / images not needed for preview

const html = `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<link rel="stylesheet" href="node_modules/katex/dist/katex.min.css">
<style>
 body{max-width:900px;margin:2rem auto;padding:0 1.5rem;
      font-family:"Noto Sans CJK SC","Source Han Sans SC",system-ui,sans-serif;
      line-height:1.75;color:#222;background:#fff;font-size:16px}
 h1{font-size:1.9rem;border-bottom:2px solid #eee;padding-bottom:.4rem}
 h2{font-size:1.5rem;border-bottom:1px solid #eee;padding-bottom:.3rem;margin-top:2.2rem}
 h3{font-size:1.2rem;margin-top:1.8rem} h4{font-size:1.05rem;margin-top:1.4rem}
 pre.code{background:#f6f8fa;border:1px solid #e1e4e8;border-radius:6px;
          padding:.8rem 1rem;overflow-x:auto;font-size:.85rem;line-height:1.5}
 code{background:#f0f2f4;padding:.1em .35em;border-radius:3px;font-size:.9em}
 pre.code code{background:none;padding:0}
 table{border-collapse:collapse;width:100%;margin:1rem 0;font-size:.92rem}
 th,td{border:1px solid #d0d7de;padding:.45rem .7rem;text-align:left;vertical-align:top}
 th{background:#f6f8fa}
 .katex-display{margin:1rem 0;overflow-x:auto;overflow-y:hidden}
 .err{color:#b00;background:#fee;padding:.2em .4em;border-radius:3px}
 blockquote{border-left:4px solid #d0d7de;margin:1rem 0;padding:.2rem 1rem;color:#555}
</style></head><body>
${body}
</body></html>`;

fs.writeFileSync("preview.html", html);
console.log("wrote preview.html  (" + html.length + " bytes)");
