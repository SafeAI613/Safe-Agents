import { describeError } from "../lib/errors";

export function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div className="banner banner-error" role="alert">
      <span>{describeError(error)}</span>
      {onRetry && (
        <button type="button" className="link" onClick={onRetry}>
          נסו שוב
        </button>
      )}
    </div>
  );
}
