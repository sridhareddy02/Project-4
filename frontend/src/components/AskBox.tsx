import { useState } from "react";
import { DEMO } from "../api";

export function AskBox({ onAsk, busy, questions, recorded }: { onAsk: (q: string) => void; busy: boolean; questions: string[]; recorded: string[] }) {
  const [text, setText] = useState("");
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (text.trim()) onAsk(text.trim());
  };
  return (
    <section className="card ask" aria-labelledby="ask-h">
      <h2 className="sr-only" id="ask-h">Ask a question</h2>
      <form onSubmit={submit}>
        <label className="sr-only" htmlFor="q">Your question about marketing performance</label>
        <input id="q" type="text" maxLength={500} value={text} onChange={(e) => setText(e.target.value)} autoComplete="off"
               placeholder={DEMO ? "Pick a recorded question below (free text needs the API)" : "Ask anything, e.g. “Why did display CAC go up in August?”"} />
        <button className="btn primary" type="submit" disabled={busy || !text.trim()}>{busy ? <><span className="spin" />Working</> : "Ask"}</button>
      </form>
      <div className="chips" aria-label="Example questions">
        {questions.map((q) => <button key={q} className="q-chip" type="button" disabled={busy} onClick={() => { setText(q); onAsk(q); }}>{q}</button>)}
      </div>
      {DEMO && recorded.length > 0 && (
        <>
          <p className="group-label">More recorded questions, including ones it should refuse</p>
          <div className="chips" style={{ marginTop: 6 }}>
            {recorded.filter((q) => !questions.includes(q)).map((q) => <button key={q} className="q-chip" type="button" disabled={busy} onClick={() => { setText(q); onAsk(q); }}>{q}</button>)}
          </div>
        </>
      )}
      <p className="hint">
        {DEMO
          ? "This page replays answers recorded from the real engine; nothing is computed in your browser. Run the API locally to ask your own questions."
          : "Answers are computed from a read-only warehouse. Each one shows its plan, its SQL, and a check that every number in the text traces back to the data."}
      </p>
    </section>
  );
}
