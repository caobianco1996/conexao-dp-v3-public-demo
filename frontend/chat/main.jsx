import React, { useEffect } from "react";
import { createRoot } from "react-dom/client";
import ChatLauncher from "../next/app/components/chat-launcher";
import ChatPanel from "../next/app/components/chat-panel";
import { useChatUnread } from "../next/app/components/chat-unread";
import "../next/app/chat.css";
import "./standalone.css";

function StandaloneChat({ fullPage }) {
  const unread = useChatUnread();
  useEffect(() => {
    document.querySelectorAll("[data-chat-unread]").forEach(element => {
      element.hidden = !unread; element.textContent = unread > 99 ? "99+" : String(unread);
    });
  }, [unread]);
  return fullPage ? <ChatPanel/> : <ChatLauncher/>;
}
if (!document.querySelector(".team-chat-mount, .floating-chat")) {
  const pageMount = document.querySelector(".chat-page-mount");
  const mount = document.createElement("div"); mount.className = "team-chat-mount";
  (pageMount || document.body).append(mount);
  createRoot(mount).render(<StandaloneChat fullPage={Boolean(pageMount)}/>);
}
