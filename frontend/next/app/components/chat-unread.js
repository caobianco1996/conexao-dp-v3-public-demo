"use client";
import { useSyncExternalStore } from "react";
let count = 0, timer = null, pending = false;
const listeners = new Set();
export function publishChatUnread(value) {
  const next = Math.max(0, Number(value) || 0);
  if (next === count) return;
  count = next;
  for (const listener of listeners) listener();
}
async function refresh() {
  if (pending || document.hidden) return;
  pending = true;
  try {
    const response = await fetch("/api/chat/unread", { credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(8000) });
    if (response.ok) publishChatUnread((await response.json()).unreadTotal);
  } catch {} finally { pending = false; }
}
function subscribe(listener) {
  listeners.add(listener);
  if (!timer) {
    refresh(); timer = setInterval(refresh, 7000);
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("focus", refresh);
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      clearInterval(timer); timer = null;
      document.removeEventListener("visibilitychange", refresh);
      window.removeEventListener("focus", refresh);
    }
  };
}
export const useChatUnread = () => useSyncExternalStore(subscribe, () => count, () => 0);
