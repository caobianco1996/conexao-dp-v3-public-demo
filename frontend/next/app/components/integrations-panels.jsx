"use client";

import { useEffect, useState } from "react";

export function SyncIcon({ name = "sync" }) {
  const paths = {
    sync: "M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.55-1L20 9M4 15l2.35 3A7 7 0 0 0 17.9 17",
    person: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
    company: "M4 21V5l8-3v19M12 8h8v13M2 21h20M7 7h2M7 11h2M7 15h2M15 11h2M15 15h2",
    records: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M8 13h8M8 17h6",
    alert: "M12 9v4M12 17h.01M10.3 3.9 1.9 18.4A1.7 1.7 0 0 0 3.4 21h17.2a1.7 1.7 0 0 0 1.5-2.6L13.7 3.9a2 2 0 0 0-3.4 0Z",
    clock: "M12 8v4l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
    settings: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2",
    archive: "M3 3h18v5H3ZM5 8v13h14V8M10 12h4",
    health: "M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z",
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.sync}/></svg>;
}

export function SyncFold({ id, title, description, badge, tone = "neutral", icon = "records", initiallyOpen = false, forceOpen = false, className = "", onOpenChange, children }) {
  const [open, setOpen] = useState(initiallyOpen);
  useEffect(() => { if (forceOpen) setOpen(true); }, [forceOpen]);
  return <details id={id} className={`sync-fold ${className}`} open={open} onToggle={event => { if (event.target !== event.currentTarget) return; setOpen(event.currentTarget.open); onOpenChange?.(event.currentTarget.open); }}>
    <summary>
      <span className="sync-panel-icon"><SyncIcon name={icon}/></span>
      <span className="sync-fold-label"><strong>{title}</strong>{description && <small>{description}</small>}</span>
      {badge && <span className={`sync-badge is-${tone}`}>{badge}</span>}
      <span className="sync-chevron" aria-hidden="true"/>
    </summary>
    <div className="sync-fold-body">{children}</div>
  </details>;
}
