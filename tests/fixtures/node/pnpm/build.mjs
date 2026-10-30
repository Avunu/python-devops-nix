// The fixture's build: a dependency from node_modules, a binary from
// node_modules/.bin (the build script calls `semver` first), and a file
// written where the target's config says outputs go.
import { mkdirSync, writeFileSync } from "node:fs";
import isNumber from "is-number";

mkdirSync("out", { recursive: true });
writeFileSync("out/x.js", `export const built = ${isNumber(5)};\n`);
