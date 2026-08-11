"use client";

import { useEffect, useRef, useState } from "react";

export default function CopyCommand({ command, compact = false, label = null }) {
  const [copied, setCopied] = useState(false);
  const resetTimer = useRef(null);

  useEffect(() => {
    return () => {
      if (resetTimer.current) clearTimeout(resetTimer.current);
    };
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      if (resetTimer.current) clearTimeout(resetTimer.current);
      resetTimer.current = setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className={`command ${compact ? "command--compact" : ""}`}>
      <span
        className={`command__prompt ${label ? "command__prompt--label" : ""}`}
        aria-hidden="true"
      >
        {label || "$"}
      </span>
      <code>{command}</code>
      <button
        className="command__copy"
        type="button"
        onClick={copy}
        aria-label={`Copy ${label ? `${label} ` : ""}command`}
      >
        {copied ? (
          <>
            <CheckIcon />
            <span>Copied</span>
          </>
        ) : (
          <>
            <CopyIcon />
            <span>Copy</span>
          </>
        )}
      </button>
      <span className="sr-only" aria-live="polite">
        {copied ? "Command copied" : ""}
      </span>
    </div>
  );
}

function CopyIcon() {
  return (
    <svg viewBox="0 0 18 18" aria-hidden="true">
      <rect x="6.2" y="5.8" width="8.3" height="8.3" rx="1.8" />
      <path d="M4.5 11.7H4A1.8 1.8 0 0 1 2.2 9.9V4A1.8 1.8 0 0 1 4 2.2h5.9A1.8 1.8 0 0 1 11.7 4v.5" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 18 18" aria-hidden="true">
      <path d="m3.2 9.1 3.4 3.4 8.2-8.2" />
    </svg>
  );
}
