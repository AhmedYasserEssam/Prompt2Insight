import {AnalyticsChat} from "@/components/analytics-chat";

export default function Home() {
  return (
    <main className="stack">
      <header>
        <h1>Prompt2Insight</h1>
        <p>Ask enterprise-data questions in English or Arabic.</p>
      </header>
      <AnalyticsChat />
    </main>
  );
}
