import React, { createContext, useCallback, useContext, useState } from "react";

// Minimal toast system: a provider + useToast() hook. Toasts slide in and
// auto-dismiss (see styles.css .toast).
const ToastCtx = createContext(() => {});

export function useToast() {
  return useContext(ToastCtx);
}

let _id = 0;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const push = useCallback((message, kind = "info", ttl = 3200) => {
    const id = ++_id;
    setToasts((t) => [...t, { id, message, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), ttl);
  }, []);

  const api = useCallback(
    Object.assign((message, kind, ttl) => push(message, kind, ttl), {
      success: (m, ttl) => push(m, "success", ttl),
      error: (m, ttl) => push(m, "error", ttl),
      info: (m, ttl) => push(m, "info", ttl),
    }),
    [push]
  );

  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="toast-wrap">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            {t.message}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
