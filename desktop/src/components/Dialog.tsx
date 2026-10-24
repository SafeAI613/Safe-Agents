import { useEffect, type ReactNode } from "react";

export function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="dialog-backdrop" role="presentation">
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title}>
        <header className="page-header">
          <h2>{title}</h2>
          <button type="button" className="link" onClick={onClose} aria-label="סגירת החלון">
            ✕
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
