#!/usr/bin/env node
// 直接调用 datawind-picker 内部的 handleRun，绕过 MCP 超时限制
import { handleRun } from "/Users/jaker/datawind-picker/src/tools/run.js";

const bookmarkId = process.argv[2];
if (!bookmarkId) {
  console.error("Usage: node run_bookmark.mjs <bookmarkId>");
  process.exit(1);
}

console.log(`[${new Date().toISOString()}] 开始运行书签 ${bookmarkId}`);
const res = await handleRun({ bookmarkId });
console.log(`[${new Date().toISOString()}] 完成`);

// 输出内容
for (const c of res.content || []) {
  if (c.type === "text") console.log(c.text);
}
