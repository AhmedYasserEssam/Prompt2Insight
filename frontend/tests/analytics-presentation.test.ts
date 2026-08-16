import assert from "node:assert/strict";
import test from "node:test";

import {
  LARGE_TABLE_ROW_COUNT,
  partitionWarnings,
  tableStartsExpanded,
} from "../lib/analytics-presentation.ts";

test("large result tables start collapsed", () => {
  assert.equal(tableStartsExpanded(LARGE_TABLE_ROW_COUNT), true);
  assert.equal(tableStartsExpanded(LARGE_TABLE_ROW_COUNT + 1), false);
});

test("recovery diagnostics are separated from analytical warnings", () => {
  const fallback =
    "A reliable generated answer was unavailable; a deterministic result summary was used.";
  const truncation = "Result rows were truncated to the configured limit.";
  const dataQuality = "Some orders have no category.";

  assert.deepEqual(partitionWarnings([fallback, truncation, dataQuality]), {
    visible: [truncation, dataQuality],
    technical: [fallback],
  });
});

test("Arabic recovery diagnostics use the same presentation classification", () => {
  const fallback =
    "تعذر إنشاء إجابة موثوقة؛ تم استخدام ملخص حتمي للنتيجة.";

  assert.deepEqual(partitionWarnings([fallback]), {
    visible: [],
    technical: [fallback],
  });
});
