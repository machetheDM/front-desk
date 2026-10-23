import { useEffect, useState } from "react";
import { Shell } from "../lib/Shell";
import { api } from "../lib/session";

interface Summary {
  messages_received: number; answered: number; answered_after_hours: number; handed_to_staff: number;
  handoff_rate: { rate: number; ci95: [number, number] }; median_first_reply_seconds: number | null;
  top_intents: [string, number][]; handoff_reasons: [string, number][]; unanswered_questions: string[];
  documents_due_for_review: { title: string; review_by: string; overdue: boolean }[];
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
      <h3>Questions the assistant could not answer from your documents</h3>
      <p className="muted">Some are missing from your documents; others are there but worded differently from how customers ask. Either way, one edit fixes it.</p>
      <ul>{s.unanswered_questions.map((q, i) => <li key={i}>{q}</li>)}</ul>
      {s.documents_due_for_review.length > 0 && (
        <>
          <h3>Documents due for review</h3>
          <p className="muted">The assistant quotes these exactly, so an out-of-date price is a wrong answer with a citation.</p>
          <ul>{s.documents_due_for_review.map((d) => <li key={d.title}>{d.title}: review by {d.review_by}{d.overdue ? " (overdue)" : ""}</li>)}</ul>
        </>
      )}
      <h3>What customers ask about</h3>
      <table><tbody>{s.top_intents.map(([k, v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}</tbody></table>
    </>
  );
}

export default function Insights() {
  return <Shell><h2>Insights</h2><Numbers /></Shell>;
}
