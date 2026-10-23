import { useEffect, useState } from "react";
import { useRouter } from "next/router";
import { Shell } from "../lib/Shell";
import { api } from "../lib/session";

interface Msg { id: string; direction: "in" | "out"; author: string; body: string; body_redacted: string | null; redactions: { kind: string }[]; created_at: string }
interface Conv { conversation: { id: string; status: string; channel: string }; messages: Msg[] }

function Thread({ id }: { id: string }) {
  const [conv, setConv] = useState<Conv | null>(null);
  const [text, setText] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const load = () => api<Conv>(`/v1/inbox/conversations/${id}`).then(setConv).catch((e: Error) => setNote(e.message));
  useEffect(() => { void load(); }, [id]);
  if (!conv) return <p className="muted">{note ?? "Loading..."}</p>;
  const send = async () => {
    await api(`/v1/inbox/conversations/${id}/reply`, { method: "POST", body: JSON.stringify({ text }) });
    setText("");
    await load();
  };
  const resolve = async () => {
    await api(`/v1/inbox/conversations/${id}/resolve`, { method: "POST", body: "{}" });
    setNote("Marked as resolved.");
  };
  return (
    <>
      <p className="muted">Channel: {conv.conversation.channel} · status: {conv.conversation.status}. Opening this conversation was recorded in the audit log.</p>
      {conv.messages.map((m) => (
        <div key={m.id} className={`msg ${m.direction}`}>
          <div className="muted" style={{ fontSize: 12 }}>{m.author} · {new Date(m.created_at).toLocaleString("en-ZA")}</div>
          {m.body}
          {m.redactions.length > 0 ? (
            <div className="redacted">The assistant saw: {m.body_redacted} ({m.redactions.map((r) => r.kind).join(", ")} removed)</div>
          ) : null}
        </div>
      ))}
      <div className="card">
        <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Reply as yourself. The customer sees your name." />
        <p><button onClick={send} disabled={!text.trim()}>Send reply</button> <button className="secondary" onClick={resolve}>Resolve</button></p>
        {note ? <p className="muted">{note}</p> : null}
      </div>
    </>
  );
}

export default function ConversationPage() {
  const router = useRouter();
  const id = typeof router.query.id === "string" ? router.query.id : null;
  return <Shell><h2>Conversation</h2>{id ? <Thread id={id} /> : <p className="muted">No conversation selected.</p>}</Shell>;
}
