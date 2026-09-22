import { readFileSync } from "node:fs";

// src/styles.css now only @imports these files (plus a small Board/Person
// block that stays in styles.css itself, see the comment there). The style
// tests assert against raw CSS text, so read the split files directly in
// import order instead of the aggregator, which node's fs cannot resolve
// @import through.
const SPLIT_FILES = ["tokens", "shell", "chat", "route", "responsive"];

export const styles = SPLIT_FILES.map((name) =>
  readFileSync(`src/styles/${name}.css`, "utf8"),
).join("\n");
