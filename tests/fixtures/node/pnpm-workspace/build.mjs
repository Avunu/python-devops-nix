// The root's build reads a workspace member, which reads its own dependency.
import { mkdirSync, writeFileSync } from "node:fs";
import types from "@fixture/types";

mkdirSync("out", { recursive: true });
writeFileSync("out/x.js", `export const built = ${types.answer};\n`);
