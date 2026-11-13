// Writes src/data/evaluation/pipeline.json from PIPELINE. Needs Node 22.18 or newer (native type stripping).
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import process from "node:process";
import { parseArgs } from "node:util";
import { pipelineFile, serialize } from "../src/data/evaluation/pipelineFile.ts";

const { values } = parseArgs({ options: { date: { type: "string" }, commit: { type: "string" }, note: { type: "string" }, out: { type: "string" } } });
if (!values.date) {
  console.error("usage: npm run export:pipeline -- --date YYYY-MM-DD [--commit <40 hex>] [--note <text>] [--out <path>]");
  process.exit(2);
}
try {
  const commit = values.commit ?? execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
  writeFileSync(values.out ?? "src/data/evaluation/pipeline.json", serialize(pipelineFile({ date: values.date, commit, note: values.note })));
} catch (e) {
  console.error(e instanceof Error ? e.message : String(e));
  process.exit(1);
}
