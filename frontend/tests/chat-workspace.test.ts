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
const chartRenderer = readFileSync(
  new URL("../components/chart-renderer.tsx", import.meta.url),
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

test("sent messages stay on the physical right in both layout directions", () => {
  assert.match(css, /\.message-user\s*\{[^}]*flex-direction:\s*row-reverse/);
  assert.match(css, /\[dir="rtl"\] \.message-user\s*\{[^}]*flex-direction:\s*row/);
});

test("the active database remains legible when its selector is disabled", () => {
  const disabledSelector = css.match(/\.header-connection select:disabled\s*\{([^}]+)\}/)?.[1] ?? "";

  assert.match(disabledSelector, /color:\s*var\(--text\)/);
  assert.match(disabledSelector, /opacity:\s*1/);
  assert.match(disabledSelector, /-webkit-text-fill-color:\s*var\(--text\)/);
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

test("a new chat is not created before a non-empty first message is submitted", () => {
  const emptyGuard = component.indexOf("if (!content || inFlight.current) return");
  const create = component.indexOf("const created = await createConversation");

  assert.notEqual(emptyGuard, -1);
  assert.notEqual(create, -1);
  assert.ok(emptyGuard < create);
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

test("suggested prompts populate the composer without submitting", () => {
  assert.match(component, /t\.suggestions\.map/);
  assert.match(component, /onClick=\{\(\) => saveDraft\(suggestion\)\}/);
});

test("sending is gated on a ready data connection", () => {
  assert.match(component, /const canQuery = activeProfile\?\.state === "ready"/);
  assert.match(component, /disabled=\{sending \|\| !draft\.trim\(\) \|\| !canQuery\}/);
});

test("the chatbot uses text-only branding and a monochrome theme", () => {
  assert.match(component, /<span className="brand-copy">/);
  assert.doesNotMatch(component, /className="brand-mark"/);
  assert.doesNotMatch(component, /className="empty-mark"/);
  assert.match(css, /--accent:\s*#111111/);
  assert.doesNotMatch(css, /#4f46e5|#6366f1|#7c3aed/);
  assert.match(chartRenderer, /const PALETTE = \["#111111", "#404040"/);
});
