import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
const component = readFileSync(
  new URL("../components/chat-workspace.tsx", import.meta.url),
  "utf8",
);
const conversationPage = readFileSync(
  new URL("../app/chat/[conversationId]/page.tsx", import.meta.url),
  "utf8",
);

test("chat fills the viewport with the composer below the scrolling thread", () => {
  const chatMain = css.match(/\.chat-main\s*\{([^}]+)\}/)?.[1] ?? "";
  const messageThread = css.match(/\.message-thread\s*\{([^}]+)\}/)?.[1] ?? "";

  assert.match(chatMain, /width:\s*100%/);
  assert.match(chatMain, /max-width:\s*none/);
  assert.match(chatMain, /margin:\s*0/);
  assert.match(chatMain, /padding:\s*0/);
  assert.match(messageThread, /min-height:\s*0/);
});

test("conversation history entries reopen the full thread", () => {
  assert.match(component, /getConversation\(conversationId\)/);
  assert.match(component, /href=\{`\/chat\/\$\{item\.id\}`\}/);
  assert.match(component, /conversation\?\.messages\.map/);
});

test("the new-chat route opens an empty thread instead of loading a UUID", () => {
  assert.match(
    conversationPage,
    /conversationId=\{conversationId === "new" \? undefined : conversationId\}/,
  );
});

test("a new chat waits for message persistence before changing routes", () => {
  const submit = component.indexOf("const result = await submitConversationMessage");
  const navigate = component.indexOf("router.replace(`/chat/${id}`)");

  assert.notEqual(submit, -1);
  assert.notEqual(navigate, -1);
  assert.ok(submit < navigate);
});

test("retrying a failed message resubmits its content with a fresh id", () => {
  assert.match(
    component,
    /retry\.current = \{content: item\.content, id: crypto\.randomUUID\(\)\}/,
  );
});

test("assistant analytics answers are not rendered twice", () => {
  assert.match(
    component,
    /item\.role !== "assistant" \|\| !item\.metadata\.analytics \? <div className="message-content">\{item\.content\}<\/div> : null/,
  );
});
