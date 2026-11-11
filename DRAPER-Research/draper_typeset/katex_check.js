#!/usr/bin/env node
/* Validate every math snippet in final.md with KaTeX (the renderer Obsidian
 * uses). Reports any snippet KaTeX fails to parse.
 *
 * Usage: node katex_check.js [file]
 */
const fs = require("fs");
const katex = require("katex");

const file = process.argv[2] || "final.md";
const text = fs.readFileSync(file, "utf8");
const lines = text.split("\n");

let display = 0, inline = 0, errors = 0;
const problems = [];

// ---- display math: $$ ... $$ (possibly multiline) ----
const stripped = [];
let i = 0;
while (i < lines.length) {
  const line = lines[i];
  const trimmed = line.trim();
  if (trimmed === "$$") {
    let j = i + 1;
    const buf = [];
    while (j < lines.length && lines[j].trim() !== "$$") { buf.push(lines[j]); j++; }
    if (j >= lines.length) {
      problems.push({ line: i + 1, msg: "unterminated $$ block" });
      errors++;
      break;
    }
    const src = buf.join("\n");
    display++;
    try {
      katex.renderToString(src, { displayMode: true, throwOnError: true, strict: false });
    } catch (e) {
      errors++;
      problems.push({ line: i + 1, src: src.slice(0, 160), msg: e.message.split("\n")[0] });
    }
    stripped.push("");
    i = j + 1;
    continue;
  }
  stripped.push(line);
  i++;
}

// ---- inline math: $ ... $ on non-code lines ----
const inlineRe = /(?<!\$)\$(?!\$)((?:\\.|[^$\\])+?)\$(?!\$)/g;
let inFence = false;
stripped.forEach((line, idx) => {
  if (line.trimStart().startsWith("```")) { inFence = !inFence; return; }
  if (inFence) return;
  let m;
  while ((m = inlineRe.exec(line)) !== null) {
    inline++;
    try {
      katex.renderToString(m[1], { displayMode: false, throwOnError: true, strict: false });
    } catch (e) {
      errors++;
      problems.push({ line: idx + 1, src: m[1], msg: e.message.split("\n")[0] });
    }
  }
});

console.log(`display math: ${display}`);
console.log(`inline math : ${inline}`);
console.log(`errors      : ${errors}`);
if (problems.length) {
  console.log("\n--- PROBLEMS ---");
  for (const p of problems) {
    console.log(`line ${p.line}: ${p.msg}`);
    if (p.src) console.log(`    src: ${p.src}`);
  }
}
process.exit(errors ? 1 : 0);
