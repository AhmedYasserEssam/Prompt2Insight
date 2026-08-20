"use client";

import {useEffect} from "react";

export function useDocumentLanguage(language: "en" | "ar") {
  useEffect(() => {
    const root = document.documentElement;
    root.lang = language;
    root.dir = language === "ar" ? "rtl" : "ltr";
  }, [language]);
}
