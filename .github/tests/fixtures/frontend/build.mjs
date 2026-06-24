// Minimal "build": emit a dist/ artifact so the smoke test has something to
// upload, exercising the shared frontend setup (node + yarn + cache + upload).
import { mkdirSync, writeFileSync } from "node:fs";

mkdirSync("dist", { recursive: true });
writeFileSync("dist/index.js", "export const ok = true;\n");
console.log("build ok");
