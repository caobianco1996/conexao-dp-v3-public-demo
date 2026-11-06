"use client";
import { useEffect, useRef, useState } from "react";
import ChatPanel from "./chat-panel";
import ChatIcon from "./chat-icon";
import { useChatUnread } from "./chat-unread";

export default function ChatLauncher({ fullPage = false, FullChatLink = "a" }) {
  const [open, setOpen] = useState(false), [mounted, setMounted] = useState(false), [expanded, setExpanded] = useState(false);
  const count = useChatUnread(), launchRef = useRef(null), panelRef = useRef(null);
  const visible = open && !fullPage;
  function minimize() { setOpen(false); launchRef.current?.focus(); }
  useEffect(() => {
    if (!visible) return;
    const frame = requestAnimationFrame(() => panelRef.current?.querySelector("textarea, .chat-search input, button")?.focus());
    const onKey = event => { if (event.key === "Escape" && !event.defaultPrevented) { event.preventDefault(); minimize(); } };
    document.addEventListener("keydown", onKey);
    return () => { cancelAnimationFrame(frame); document.removeEventListener("keydown", onKey); };
  }, [visible]);
  return <div className="floating-chat" hidden={fullPage}>
    <div ref={panelRef} id="team-chat-popup" className={`floating-chat-panel${expanded ? " is-expanded" : ""}`} hidden={!visible} role="dialog" aria-label="Chat rápido da equipe">
      {mounted && <ChatPanel compact expanded={expanded} active={visible} onClose={minimize} onExpand={() => setExpanded(value => !value)} FullChatLink={FullChatLink}/>}
    </div>
    <button ref={launchRef} className={`floating-chat-launch${visible ? " is-open" : ""}`} type="button" onClick={() => { setMounted(true); setOpen(value => !value); }} aria-label={visible ? "Minimizar chat da equipe" : `Chat da equipe${count ? `, ${count} mensagens não lidas` : ""}`} aria-expanded={visible} aria-controls="team-chat-popup">
      <ChatIcon name={visible ? "minus" : "chat"} size={20}/><span>{visible ? "Minimizar" : "Chat da equipe"}</span>{count > 0 && <b>{count > 99 ? "99+" : count}</b>}
    </button>
  </div>;
}
