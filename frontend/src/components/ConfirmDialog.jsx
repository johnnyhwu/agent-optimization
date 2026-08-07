import React, { useState } from "react";
import Modal from "./Modal.jsx";
import Button from "./ui/Button.jsx";
import { IconAlert } from "./icons.jsx";

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
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="danger" onClick={confirm} loading={busy}>
            {busy ? "Deleting…" : confirmLabel}
          </Button>
        </>
      }
    >
      {/* The mark is the point: this dialog looks like every other one until you
          read it, and it is the only one whose confirm cannot be undone. */}
      <div className="confirm-body">
        <span className="confirm-mark"><IconAlert size={18} /></span>
        <div>
          <p className="confirm-message">{message}</p>
          {detail && <p className="confirm-detail">{detail}</p>}
        </div>
      </div>
      {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}
    </Modal>
  );
}
