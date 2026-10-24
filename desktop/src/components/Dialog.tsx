import { useEffect, type ReactNode } from "react";

/** Modal dialog. `closable={false}` blocks Escape and ✕, e.g. while an install runs. */
export function Dialog({
  title,
  onClose,
  closable = true,
  children,
}: {
  title: string;
  onClose: () => void;
  closable?: boolean;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!closable) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, closable]);
  return (
    <div className="dialog-backdrop" role="presentation">
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title}>
        <header className="page-header">
          <h2>{title}</h2>
          <button
            type="button"
            className="link"
            onClick={onClose}
            disabled={!closable}
            aria-label="סגירת החלון"
          >
            ✕
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
