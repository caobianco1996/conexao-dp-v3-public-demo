"use client";
import { usePathname } from "next/navigation";
import Link from "next/link";
import ChatLauncher from "./chat-launcher";

export default function FloatingChat() {
  return <ChatLauncher fullPage={usePathname() === "/central-dp/chat"} FullChatLink={Link}/>;
}
