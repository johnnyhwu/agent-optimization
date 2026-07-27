import React from "react";

// Left column (§6.13): question list, correct/incorrect colored, "only wrong"
// filter. is_incorrect is computed by the backend per the selected mode.
export default function QuestionList({ results, activeId, onlyWrong, setOnlyWrong, onPick }) {
  const shown = onlyWrong ? results.filter((r) => r.is_incorrect) : results;
  return (
    <div className="col">
      <h4>
        Questions
        <label style={{ float: "right", fontWeight: 400, fontSize: 12 }}>
          <input type="checkbox" checked={onlyWrong} onChange={(e) => setOnlyWrong(e.target.checked)} /> only wrong
        </label>
      </h4>
      {shown.map((r) => {
        const cls = r.status === "failed" ? "failed" : r.is_incorrect ? "incorrect" : "correct";
        return (
          <div
            key={r.id}
            className={`qitem ${activeId === r.id ? "active" : ""}`}
            onClick={() => onPick(r)}
          >
            <span className={`dot ${cls}`} />
            <div>
              <div>{r.question.slice(0, 40)}</div>
              <div className="qid">
                {r.question_id}
                {r.status === "failed" ? " · failed" : ""}
                {r.is_incorrect && r.status !== "failed" ? " · incorrect" : ""}
              </div>
              {/* A bare "failed" says nothing once the agent is a real service. */}
              {r.status === "failed" && r.error_message && (
                <div className="qerror" title={r.error_message}>
                  {r.error_message.slice(0, 80)}
                </div>
              )}
            </div>
          </div>
        );
      })}
      {shown.length === 0 && <div className="notflagged">No questions match.</div>}
    </div>
  );
}
