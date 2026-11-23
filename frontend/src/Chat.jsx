import { useState, useRef, useEffect } from "react";
import { askQuestion } from "./api.js";

const STARTER_QUESTIONS = [
  "Which services contributed most to my costs?",
  "Which resources have the highest potential savings?",
  "How could I reduce my monthly cost by about 30%?",
  "What risks should I consider before stopping a resource?",
];

export default function Chat({ analysis }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [conversationId, setConversationId] = useState(null);
  const [suggestions, setSuggestions] = useState(STARTER_QUESTIONS);
  const [open, setOpen] = useState(false);
  const [disclaimer, setDisclaimer] = useState(null);
  const endRef = useRef(null);

  const asked = new Set(messages.filter((m) => m.role === "user").map((m) => m.text));
  const visibleSuggestions = suggestions.filter((q) => !asked.has(q));

  // New upload → fresh conversation
  useEffect(() => {
    setMessages([]);
    setConversationId(null);
    setSuggestions(STARTER_QUESTIONS);
    setError(null);
  }, [analysis.analysisId]);

  // Lets the CSS shrink the page so the drawer doesn't cover it
  useEffect(() => {
    document.body.classList.toggle("chat-open", open);
    return () => document.body.classList.remove("chat-open");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, loading]);

  async function send(question) {
    question = question.trim();
    if (question.length < 3 || loading) return;
    setInput("");
    setError(null);
    setMessages((m) => [...m, { role: "user", text: question }]);
    setLoading(true);
    try {
      const res = await askQuestion(analysis, question, conversationId);
      setConversationId(res.conversationId);
      setMessages((m) => [...m, { role: "assistant", text: res.answer, sources: res.sources }]);
      if (res.disclaimer) setDisclaimer(res.disclaimer);
      if (res.suggestedFollowUps?.length) setSuggestions(res.suggestedFollowUps);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {!open && (
        <button className="chat-launcher" onClick={() => setOpen(true)}>💬 Ask about your bill</button>
      )}
      <aside className={`chat-drawer ${open ? "open" : ""}`} aria-hidden={!open}>
        <div className="chat-header">
          <h3>Cost assistant</h3>
          <button className="chat-close" onClick={() => setOpen(false)} aria-label="Close chat">✕</button>
        </div>
        {messages.length === 0 && (
          <p className="muted">Ask a question in plain English. Answers are based only on this uploaded report.</p>
        )}
        <div className="messages">
          {messages.map((m, i) => (
            <div key={i} className={`msg msg-${m.role}`}>
              <div>{m.text}</div>
              {m.sources?.length > 0 && (
                <div className="msg-meta">Based on: {m.sources.map((s) => s.label).join(", ")}</div>
              )}
            </div>
          ))}
          {loading && (
            <div className="msg msg-assistant status"><span className="spinner" /> Thinking…</div>
          )}
          <div ref={endRef} />
        </div>
        {error && <p className="error">{error}</p>}
        <div className="chips">
          {visibleSuggestions.map((q) => (
            <button key={q} className="chip" onClick={() => send(q)} disabled={loading}>{q}</button>
          ))}
        </div>
        <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="e.g. Why did my EC2 cost go up?"
            maxLength={2000}
            disabled={loading}
          />
          <button type="submit" disabled={loading || input.trim().length < 3}>Send</button>
        </form>
        {disclaimer && <p className="msg-meta">{disclaimer}</p>}
      </aside>
    </>
  );
}
