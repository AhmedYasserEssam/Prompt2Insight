import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";

const workspace = readFileSync(
  new URL("../components/connection-workspace.tsx", import.meta.url),
  "utf8",
);
const chatPage = readFileSync(
  new URL("../app/chat/page.tsx", import.meta.url),
  "utf8",
);

test("selecting a ready connection opens the modern chat", () => {
  assert.match(workspace, /localStorage\.setItem\(SELECTED_CONNECTION_KEY, profile\.id\)/);
  assert.match(workspace, /router\.push\("\/chat\/"\)/);
  assert.doesNotMatch(workspace, /AnalyticsChat|setConversationId|selectConnection/);
});

test("the chat root route opens a new chat workspace", () => {
  assert.match(chatPage, /<ChatWorkspace \/>/);
});
