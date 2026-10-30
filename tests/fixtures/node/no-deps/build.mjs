import { mkdirSync, writeFileSync } from "node:fs";

mkdirSync("out", { recursive: true });
writeFileSync("out/x.js", "export const built = true;\n");
