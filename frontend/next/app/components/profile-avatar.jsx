"use client";

import { useCallback, useEffect, useState } from "react";

export const PROFILE_AVATAR_KEY = "conexao-dp-profile-avatar";
const PROFILE_AVATAR_EVENT = "conexao-dp-profile-avatar-change";
const PROFILE_MOOD_EVENT = "conexao-dp-profile-mood-change";
const PROFILE_MOOD_KEY = "conexao-dp-home-mood";
const PROFILE_MOODS = ["irritated", "confused", "happy", "melancholy", "anxious", "serene", "enthusiastic", "exhausted"];
const profileStorageKey = identity => `${PROFILE_AVATAR_KEY}:${identity || "current"}`;
const profileMoodStorageKey = identity => `${PROFILE_MOOD_KEY}:${identity || "current"}`;
const saoPauloDateKey = () => {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/Sao_Paulo", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
};

export const PROFILE_AVATARS = Array.from({ length: 16 }, (_, index) => {
  const number = index + 1;
  const gender = number <= 8 ? "man" : "woman";
  const avatarNumber = number <= 8 ? number : number - 8;
  return {
    id: `${gender}-${avatarNumber}`,
    assetPrefix: `${gender}-${avatarNumber}`,
    label: `Avatar ${String(number).padStart(2, "0")}`,
  };
});

export function profileAvatarById(id) {
  return PROFILE_AVATARS.find(avatar => avatar.id === id) || PROFILE_AVATARS[0];
}

export function saveProfileAvatar(id, identity = "current") {
  const avatar = profileAvatarById(id);
  const key = profileStorageKey(identity);
  try { window.localStorage.setItem(key, avatar.id); } catch {}
  window.dispatchEvent(new CustomEvent(PROFILE_AVATAR_EVENT, { detail: { key, id: avatar.id } }));
  return avatar.id;
}

export function useProfileAvatar(identity = "current") {
  const [avatarId, setAvatarId] = useState(PROFILE_AVATARS[0].id);
  useEffect(() => {
    const key = profileStorageKey(identity);
    const readSaved = () => { try { return window.localStorage.getItem(key); } catch { return null; } };
    setAvatarId(profileAvatarById(readSaved()).id);
    const update = event => {
      if (event.detail?.key === key) setAvatarId(profileAvatarById(event.detail.id).id);
    };
    const onStorage = event => { if (event.key === key) setAvatarId(profileAvatarById(event.newValue).id); };
    window.addEventListener(PROFILE_AVATAR_EVENT, update);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(PROFILE_AVATAR_EVENT, update);
      window.removeEventListener("storage", onStorage);
    };
  }, [identity]);
  const chooseAvatar = useCallback(id => setAvatarId(saveProfileAvatar(id, identity)), [identity]);
  return { avatar: profileAvatarById(avatarId), chooseAvatar };
}

export function readSavedProfileMood(identity = "current") {
  try {
    const saved = JSON.parse(window.localStorage.getItem(profileMoodStorageKey(identity)) || "null");
    const mood = saved?.mood;
    if (saved?.day !== saoPauloDateKey()) return null;
    if (PROFILE_MOODS.includes(mood)) return mood;
    return ({ ok: "serene", warning: "confused", critical: "anxious" })[mood] || null;
  } catch { return null; }
}

export function readProfileMood(identity = "current") {
  return readSavedProfileMood(identity) || "serene";
}

export function saveProfileMood(choice, expression, identity = "current") {
  const key = profileMoodStorageKey(identity);
  const saved = { day: saoPauloDateKey(), mood: choice, expression };
  try { window.localStorage.setItem(key, JSON.stringify(saved)); } catch {}
  window.dispatchEvent(new CustomEvent(PROFILE_MOOD_EVENT, { detail: { key, expression } }));
}

export function useProfileMood(identity = "current") {
  const [mood, setMood] = useState("ok");
  useEffect(() => {
    const key = profileMoodStorageKey(identity);
    const update = () => setMood(readProfileMood(identity));
    update();
    const onChange = event => { if (event.detail?.key === key) update(); };
    const onStorage = event => { if (event.key === key) update(); };
    window.addEventListener(PROFILE_MOOD_EVENT, onChange);
    window.addEventListener("storage", onStorage);
    return () => { window.removeEventListener(PROFILE_MOOD_EVENT, onChange); window.removeEventListener("storage", onStorage); };
  }, [identity]);
  return mood;
}

export function AvatarPortrait({ avatarId, className = "", size = 38, title, mood = "ok", expression = "" }) {
  const avatar = profileAvatarById(avatarId);
  if (PROFILE_MOODS.includes(expression)) {
    return <img
      className={`profile-avatar-portrait ${className}`.trim()}
      style={{ width: size, height: size, flex: `0 0 ${size}px` }}
      src={`/avatars/welcome-mascots/${avatar.assetPrefix}-${expression}.png`}
      alt={title || `${avatar.label}, ${expression}`}
      title={title || `${avatar.label}, ${expression}`}
      loading="lazy"
    />;
  }
  const expressionState = ({ irritated: "critical", confused: "warning", happy: "ok", melancholy: "warning", anxious: "critical", serene: "ok", enthusiastic: "ok", exhausted: "warning" })[expression];
  const state = ["ok", "warning", "critical"].includes(mood) ? mood : expressionState || "ok";
  return <img
    className={`profile-avatar-portrait ${className}`.trim()}
    style={{ width: size, height: size, flex: `0 0 ${size}px` }}
    src={`/avatars/${avatar.assetPrefix}-${state}.png?v=5`}
    alt={title || avatar.label}
    title={title || avatar.label}
    loading="lazy"
  />;
}
