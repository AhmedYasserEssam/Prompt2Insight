const TECHNICAL_RECOVERY_WARNINGS = new Set([
  "The query was repaired and fully revalidated before execution.",
  "تم إصلاح الاستعلام وإعادة التحقق منه قبل التنفيذ.",
  "The answer was regenerated once after validation failed.",
  "تمت إعادة إنشاء الإجابة مرة واحدة بعد فشل التحقق.",
  "A reliable generated answer was unavailable; a deterministic result summary was used.",
  "تعذر إنشاء إجابة موثوقة؛ تم استخدام ملخص حتمي للنتيجة.",
  "The invalid chart was omitted; the query result remains available.",
  "تم حذف الرسم البياني غير الصالح؛ تظل نتيجة الاستعلام متاحة.",
]);

export const LARGE_TABLE_ROW_COUNT = 10;

export function tableStartsExpanded(rowCount: number): boolean {
  return rowCount <= LARGE_TABLE_ROW_COUNT;
}

export function partitionWarnings(warnings: string[]): {
  visible: string[];
  technical: string[];
} {
  return warnings.reduce(
    (partitioned, warning) => {
      const destination = TECHNICAL_RECOVERY_WARNINGS.has(warning)
        ? partitioned.technical
        : partitioned.visible;
      destination.push(warning);
      return partitioned;
    },
    {visible: [] as string[], technical: [] as string[]},
  );
}
