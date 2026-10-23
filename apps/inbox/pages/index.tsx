import Link from "next/link";
import { useEffect, useState } from "react";
import { Shell } from "../lib/Shell";
import { api } from "../lib/session";

interface Handoff { handoff_id: string; conversation_id: string; reason: string; summary: string; created_at: string; assigned_to: string; channel: string }

const REASON: Record<string, string> = {
  human_requested: "Asked for a person", negotiation: "Discount / price", unanswerable: "Not in our documents",
  booking_commitment: "Booking or visit", complaint: "Complaint", fault: "Fault", refund: "Refund",
  out_of_scope: "Outside the business", verification_needed: "Order check", model_unavailable: "Assistant unavailable",
  validation_failed: "Needs a careful answer", cost_cap: "Assistant budget reached", injection: "Flagged message",
};

function Queue() {
  const [items, setItems] = useState<Handoff[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<Handoff[]>("/v1/inbox/handoffs").then(setItems).catch((e: Error) => setError(e.message)); }, []);
  if (error) return <p className="error">{error}</p>;
  if (!items) return <p className="muted">Loading...</p>;
  if (items.length === 0) return <p className="muted">Nothing waiting. The assistant is handling everything it can.</p>;
  return (
    <>
      {items.map((h) => (
        <div className="card" key={h.handoff_id}>
          <div className="reason">{REASON[h.reason] ?? h.reason} · {h.channel} · for {h.assigned_to}</div>
          <p style={{ whiteSpace: "pre-wrap" }}>{h.summary}</p>
          <Link href={`/conversation?id=${h.conversation_id}`}>Open conversation</Link>
          <span className="muted"> · {new Date(h.created_at).toLocaleString("en-ZA")}</span>
        </div>
      ))}
    </>
  );
}

export default function Home() {
  return <Shell><h2>Handed to staff</h2><Queue /></Shell>;
}
