import React, { useState } from "react";
import Modal from "./Modal.jsx";

// Confirmation for destructive actions. Deletes here cascade (an eval set takes
// its whole run history with it), so the caller passes a `detail` line spelling
// out what else goes — the count is the part people misjudge.
export default function ConfirmDialog({
  title,
  message,
  detail,
  confirmLabel = "Delete",
  onConfirm,
  onClose,
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  return (
    <Modal
      title={title}
      onClose={busy ? () => {} : onClose}
      width={460}
      footer={
        <>
          <button onClick={onClose} disabled={busy}>Cancel</button>
          <button className="danger" onClick={confirm} disabled={busy}>
            {busy ? "Deleting…" : confirmLabel}
          </button>
        </>
      }
    >
      <p style={{ margin: "0 0 8px" }}>{message}</p>
      {detail && <p className="muted" style={{ margin: 0, fontSize: 13 }}>{detail}</p>}
      {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}
    </Modal>
  );
}
