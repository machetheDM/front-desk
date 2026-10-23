import { useEffect, useState } from "react";
import { Shell } from "../lib/Shell";
import { api } from "../lib/session";

interface Summary {
  messages_received: number; answered: number; answered_after_hours: number; handed_to_staff: number;
  handoff_rate: { rate: number; ci95: [number, number] }; median_first_reply_seconds: number | null;
  top_intents: [string, number][]; handoff_reasons: [string, number][]; unanswered_questions: string[];
}

const pct = (x: number) => `${Math.round(x * 100)}%`;

function Numbers() {
  const [s, setS] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<Summary>("/v1/insights/summary?days=30").then(setS).catch((e: Error) => setError(e.message)); }, []);
  if (error) return <p className="error">{error}</p>;
  if (!s) return <p className="muted">Loading...</p>;
  return (
    <>
      <div className="grid">
        <div className="card"><div className="big">{s.answered_after_hours}</div><div className="muted">answered after hours (30 days)</div></div>
        <div className="card"><div className="big">{s.handed_to_staff}</div><div className="muted">handed to staff ({pct(s.handoff_rate.rate)}, 95% CI {pct(s.handoff_rate.ci95[0])} to {pct(s.handoff_rate.ci95[1])})</div></div>
        <div className="card"><div className="big">{s.median_first_reply_seconds ?? "-"} s</div><div className="muted">median first reply</div></div>
        <div className="card"><div className="big">{s.messages_received}</div><div className="muted">messages received</div></div>
      </div>
      <h3>Questions your documents do not answer</h3>
      <p className="muted">Each one is a missing FAQ entry. Add it, and the assistant can answer it next time.</p>
      <ul>{s.unanswered_questions.map((q, i) => <li key={i}>{q}</li>)}</ul>
      <h3>What customers ask about</h3>
      <table><tbody>{s.top_intents.map(([k, v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}</tbody></table>
    </>
  );
}

export default function Insights() {
  return <Shell><h2>Insights</h2><Numbers /></Shell>;
}
