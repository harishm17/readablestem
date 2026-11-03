// Speak MathML with Speech Rule Engine (the speech engine in MathJax and ChromeVox).
//
//   node sre_batch.js SRE_DIR < in.jsonl > out.jsonl
//
// in:  {"id": "...", "mathml": "<math>...</math>"}
// out: {"id": "...", "speech": {"clearspeak": "...", "mathspeak": "..."}, "error": null|"..."}
// SRE_DIR is the directory whose node_modules holds speech-rule-engine.
"use strict";
const path = require("path");
const readline = require("readline");

const sre = require(path.join(path.resolve(process.argv[2]), "node_modules", "speech-rule-engine"));
const STYLES = [
  ["clearspeak", { domain: "clearspeak", style: "default" }],
  ["mathspeak", { domain: "mathspeak", style: "default" }],
];

async function main() {
  const items = [];
  const rl = readline.createInterface({ input: process.stdin });
  for await (const line of rl) if (line.trim()) items.push(JSON.parse(line));
  const results = items.map((item) => ({ id: item.id, speech: {}, error: null }));
  for (const [name, opts] of STYLES) {
    await sre.setupEngine({ ...opts, locale: "en", modality: "speech" });
    items.forEach((item, i) => {
      try {
        results[i].speech[name] = sre.toSpeech(item.mathml);
      } catch (e) {
        results[i].error = String(e && e.message ? e.message : e);
      }
    });
  }
  process.stdout.write(results.map((r) => JSON.stringify(r)).join("\n") + "\n");
}
main().catch((e) => {
  console.error(String(e));
  process.exit(1);
});
