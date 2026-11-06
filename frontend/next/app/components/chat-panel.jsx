"use client";

import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AvatarPortrait, PROFILE_AVATAR_KEY, readSavedProfileMood, saveProfileAvatar, saveProfileMood } from "./profile-avatar";
import ChatIcon from "./chat-icon";
import { publishChatUnread, useChatUnread } from "./chat-unread";
import { MAX_BODY, FILE_ACCEPT, uid, chatDraftSession, messageTime, fullStamp, conversationTime, dayKey, dayLabel, fileSize, mergeMessages, readableCursor, prepareFile, chatApi as api, chatJson as json, chatError } from "./chat-utils";

const emoji = ["😊", "👍", "❤️", "🙏", "✅", "👏", "🎉", "💪", "😄", "🤔", "👀", "📌", "📎", "📅", "☕", "✨"];
const normalize = text => String(text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
function IconButton({ icon, label, ...props }) { return <button type="button" className="chat-icon-button" aria-label={label} title={label} {...props}><ChatIcon name={icon}/></button>; }
function MessageText({ text }) {
  return <p>{text.split(/(https?:\/\/[^\s<>]+)/g).map((part, index) => /^https?:\/\//i.test(part) ? <a key={index} href={part} target="_blank" rel="noreferrer">{part}</a> : part)}</p>;
}
function ConversationAvatar({ group, avatarId, moodId, name, size = 42 }) {
  return group ? <span className="chat-group-avatar" style={{ width: size, height: size }}><ChatIcon name="people" size={size * .48}/></span> : <AvatarPortrait avatarId={avatarId} expression={moodId} size={size} title={`Avatar de ${name || "participante"}`}/>;
}

export default function ChatPanel({ compact = false, expanded = false, onClose, onExpand, active = true, FullChatLink = "a" }) {
  const [auth, setAuth] = useState(null), [items, setItems] = useState([]), [people, setPeople] = useState([]);
  const [selected, setSelected] = useState(""), [room, setRoom] = useState(null), [messages, setMessages] = useState([]);
  const [query, setQuery] = useState(""), [unreadOnly, setUnreadOnly] = useState(false), [draft, setDraft] = useState(""), [files, setFiles] = useState([]);
  const [error, setError] = useState(""), [loading, setLoading] = useState(true), [roomLoading, setRoomLoading] = useState(false), [olderLoading, setOlderLoading] = useState(false);
  const [sendingRooms, setSendingRooms] = useState(new Set()), [attachingRooms, setAttachingRooms] = useState(new Set());
  const [composing, setComposing] = useState(false), [composeKind, setComposeKind] = useState("DIRECT"), [peopleQuery, setPeopleQuery] = useState("");
  const [groupTitle, setGroupTitle] = useState(""), [chosen, setChosen] = useState([]), [groupBusy, setGroupBusy] = useState(false);
  const [readBoundary, setReadBoundary] = useState(0), [atBottom, setAtBottom] = useState(true), [newCount, setNewCount] = useState(0);
  const [emojiOpen, setEmojiOpen] = useState(false), [dragging, setDragging] = useState(false), [connection, setConnection] = useState("connecting"), [announcement, setAnnouncement] = useState("");
  const unread = useChatUnread();
  const rootRef = useRef(null), scrollRef = useRef(null), inputRef = useRef(null), fileInputRef = useRef(null), emojiRef = useRef(null);
  const selectedRef = useRef(""), roomRef = useRef(null), messagesRef = useRef([]), activeRef = useRef(active), aliveRef = useRef(true), queryRef = useRef(query);
  const drafts = useRef({}), attachments = useRef({}), pendingSends = useRef(new Map()), sendingLock = useRef(new Set()), attachingLock = useRef(new Set());
  const seen = useRef(new Map()), readLocks = useRef(new Set()), listSequence = useRef(0), searchRequested = useRef(""), roomSequence = useRef(0), roomAbort = useRef(null);
  const pollLock = useRef(false), scrollAction = useRef(null), nearBottom = useRef(true), groupKey = useRef(null), pickQueue = useRef(Promise.resolve()), pickCounts = useRef(new Map()), dragDepth = useRef(0), focusRoom = useRef(false);
  activeRef.current = active; queryRef.current = query;
  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false; roomAbort.current?.abort(); }; }, []);

  const persist = useCallback((id = selectedRef.current) => {
    if (!auth) return;
    try {
      sessionStorage.setItem(`v3-chat-selected:${auth.userId}`, id);
      sessionStorage.setItem(`v3-chat-draft:${auth.userId}`, JSON.stringify(drafts.current));
    } catch {}
  }, [auth]);

  const chooseRoom = useCallback(id => {
    roomAbort.current?.abort(); roomSequence.current++;
    selectedRef.current = id; roomRef.current = null; messagesRef.current = [];
    setSelected(id); setRoom(null); setMessages([]); setRoomLoading(Boolean(id)); setOlderLoading(false);
    setDraft(drafts.current[id] || ""); setFiles(attachments.current[id] || []);
    setComposing(false); setEmojiOpen(false); setError(""); setNewCount(0); setAtBottom(true); nearBottom.current = true;
    focusRoom.current = Boolean(id);
    if (!id) requestAnimationFrame(() => rootRef.current?.querySelector(".chat-search input")?.focus({ preventScroll: true }));
    if (!compact && window.location.pathname === "/central-dp/chat") {
      const url = new URL(window.location.href);
      if (id) url.searchParams.set("id", id); else url.searchParams.delete("id");
      window.history.replaceState(null, "", url.pathname + url.search);
    }
    persist(id);
  }, [persist, compact]);

  const refreshList = useCallback(async () => {
    const sequence = ++listSequence.current, search = queryRef.current;
    const data = await api(`/api/chat/conversations?q=${encodeURIComponent(search)}`);
    if (!aliveRef.current || sequence !== listSequence.current || search !== queryRef.current) return;
    setItems(data.items || []); publishChatUnread(data.unreadTotal); setLoading(false);
    return data;
  }, []);

  useEffect(() => {
    let alive = true;
    api("/api/chat").then(data => {
      if (!alive) return;
      let saved = {}, savedId = "";
      try {
        saved = JSON.parse(sessionStorage.getItem(`v3-chat-draft:${data.userId}`) || sessionStorage.getItem(`v3-chat-drafts:${data.userId}`) || "{}");
        if (!saved || Array.isArray(saved) || typeof saved !== "object") saved = {};
        savedId = (!compact && new URLSearchParams(window.location.search).get("id")) || sessionStorage.getItem(`v3-chat-selected:${data.userId}`) || "";
      } catch {}
      const session = chatDraftSession(data.userId, saved);
      drafts.current = session.text; attachments.current = session.files;
      sendingLock.current = session.sending; attachingLock.current = session.attaching; pendingSends.current = session.pending;
      setSendingRooms(new Set(session.sending)); setAttachingRooms(new Set(session.attaching));
      setAuth(data); publishChatUnread(data.unreadTotal);
      if (savedId) chooseRoom(savedId);
      let localAvatar = null;
      try { localAvatar = window.localStorage.getItem(`${PROFILE_AVATAR_KEY}:${data.userId}`); } catch {}
      saveProfileAvatar(localAvatar || data.avatarId, data.userId);
      const moodId = readSavedProfileMood(data.userId) || readSavedProfileMood("current") || data.moodId || "serene";
      saveProfileMood(moodId, moodId, data.userId);
      if ((localAvatar && localAvatar !== data.avatarId) || moodId !== data.moodId) api("/api/chat/profile", json(data.csrf, { ...(localAvatar ? { avatarId: localAvatar } : {}), moodId })).catch(() => {});
      setConnection("ready");
    }).catch(e => { if (alive) { setError(chatError(e)); setConnection("reconnecting"); setLoading(false); } });
    api("/api/chat/people").then(data => { if (alive) setPeople(data.items || []); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!active || !auth || searchRequested.current === query) return;
    searchRequested.current = query;
    const timer = setTimeout(() => refreshList().catch(e => { if (aliveRef.current) { setError(chatError(e)); setLoading(false); } }), query ? 200 : 0);
    return () => clearTimeout(timer);
  }, [query, active, auth, refreshList]);

  const loadRoom = useCallback(async (id, older = false) => {
    if (!id || !activeRef.current || (older && olderLoading)) return;
    roomAbort.current?.abort(); const controller = new AbortController(); roomAbort.current = controller;
    const sequence = ++roomSequence.current;
    const box = scrollRef.current;
    const before = older ? messagesRef.current[0]?.id : null;
    const previousHeight = box?.scrollHeight || 0, previousTop = box?.scrollTop || 0;
    older ? setOlderLoading(true) : setRoomLoading(true);
    setError("");
    try {
      const data = await api(`/api/chat/conversations/${encodeURIComponent(id)}/messages?limit=50${before ? `&before=${before}` : ""}`, { signal: controller.signal });
      if (!aliveRef.current || selectedRef.current !== id || sequence !== roomSequence.current) return;
      const rows = older ? mergeMessages(data.messages, messagesRef.current) : data.messages || [];
      roomRef.current = data; messagesRef.current = rows; setRoom(data); setMessages(rows);
      if (!older) setReadBoundary(data.lastReadMessageId || 0);
      publishChatUnread(data.unreadTotal); setConnection("ready");
      scrollAction.current = older ? { id, type: "older", previousHeight, previousTop } : { id, type: "initial", cursor: data.lastReadMessageId || 0, unreadBeforePage: data.unreadBeforePage };
    } catch (e) {
      if (e.name !== "AbortError" && selectedRef.current === id && sequence === roomSequence.current && aliveRef.current) {
        if (e.status === 404) chooseRoom(""); else setConnection("reconnecting"); setError(chatError(e));
      }
    } finally {
      if (aliveRef.current && sequence === roomSequence.current) { setRoomLoading(false); setOlderLoading(false); }
    }
  }, [olderLoading, chooseRoom]);
  useEffect(() => { if (selected && active && auth && !roomRef.current) loadRoom(selected); }, [selected, active, auth]);

  const appendMessages = useCallback((id, data, forceBottom = false) => {
    if (selectedRef.current !== id || !roomRef.current || !aliveRef.current) return;
    const known = new Set(messagesRef.current.map(message => message.id));
    const fresh = (data.messages || []).filter(message => !known.has(message.id));
    const nextRoom = { ...roomRef.current, members: data.members || roomRef.current.members, lastReadMessageId: Math.max(roomRef.current.lastReadMessageId || 0, data.lastReadMessageId || 0) };
    roomRef.current = nextRoom; setRoom(nextRoom);
    if (!fresh.length) return;
    messagesRef.current = mergeMessages(messagesRef.current, fresh);
    if (forceBottom || nearBottom.current) scrollAction.current = { id, type: "bottom" };
    else setNewCount(value => value + fresh.filter(message => message.senderId !== auth?.userId).length);
    setMessages(messagesRef.current);
  }, [auth?.userId]);

  const synchronize = useCallback(async () => {
    if (!activeRef.current || document.hidden || !auth || pollLock.current) return;
    pollLock.current = true;
    try {
      const id = selectedRef.current, currentRoom = roomRef.current;
      await Promise.all([
        refreshList(),
        id && currentRoom ? api(`/api/chat/conversations/${encodeURIComponent(id)}/messages?since=${messagesRef.current.at(-1)?.id || 0}&limit=100`).then(data => appendMessages(id, data)) : Promise.resolve(),
      ]);
      if (aliveRef.current) setConnection("ready");
      if (aliveRef.current && selectedRef.current) {
        const currentId = selectedRef.current;
        setDraft(drafts.current[currentId] || ""); setFiles(attachments.current[currentId] || []);
        setSendingRooms(new Set(sendingLock.current)); setAttachingRooms(new Set(attachingLock.current));
      }
    } catch { if (aliveRef.current) setConnection("reconnecting"); }
    finally { pollLock.current = false; }
  }, [auth, appendMessages, refreshList]);
  useEffect(() => {
    if (!active || !auth) return;
    synchronize();
    const timer = setInterval(synchronize, 4000);
    window.addEventListener("focus", synchronize); document.addEventListener("visibilitychange", synchronize);
    return () => { clearInterval(timer); window.removeEventListener("focus", synchronize); document.removeEventListener("visibilitychange", synchronize); };
  }, [active, auth, synchronize]);

  const markVisible = useCallback(async () => {
    const id = selectedRef.current, current = roomRef.current, box = scrollRef.current;
    if (!activeRef.current || document.hidden || !document.hasFocus() || !auth || !box || !box.getClientRects().length || current?.conversation?.id !== id || readLocks.current.has(id)) return;
    const bounds = box.getBoundingClientRect();
    let visible = seen.current.get(id); if (!visible) { visible = new Set(); seen.current.set(id, visible); }
    for (const element of box.querySelectorAll("[data-message-id]")) {
      const rectangle = element.getBoundingClientRect();
      if (rectangle.bottom > bounds.top && rectangle.top < bounds.bottom) visible.add(Number(element.dataset.messageId));
    }
    const cursor = readableCursor(messagesRef.current, visible, current.lastReadMessageId || 0, auth.userId, current.unreadBeforePage);
    if (cursor <= (current.lastReadMessageId || 0)) return;
    readLocks.current.add(id);
    try {
      const data = await api(`/api/chat/conversations/${encodeURIComponent(id)}/read`, json(auth.csrf, { messageId: cursor }));
      publishChatUnread(data.unreadTotal);
      if (selectedRef.current === id && roomRef.current) {
        const next = { ...roomRef.current, lastReadMessageId: Math.max(roomRef.current.lastReadMessageId || 0, cursor) };
        roomRef.current = next; setRoom(next);
      }
      refreshList().catch(() => {});
    } catch { /* Retry visible read receipts on the next refresh or scroll. */ }
    finally { readLocks.current.delete(id); }
  }, [auth, refreshList]);

  useLayoutEffect(() => {
    const box = scrollRef.current, action = scrollAction.current;
    if (!active || !box || !action || action.id !== selected) return;
    scrollAction.current = null;
    if (action.type === "older") box.scrollTop = action.previousTop + box.scrollHeight - action.previousHeight;
    else if (action.type === "initial") {
      const firstUnread = messages.find(message => message.id > action.cursor && message.senderId !== auth?.userId);
      const target = firstUnread && box.querySelector(`[data-message-id="${firstUnread.id}"]`);
      if (target) box.scrollTop = Math.max(0, target.offsetTop - box.offsetTop - 40);
      else box.scrollTop = box.scrollHeight;
    } else box.scrollTop = box.scrollHeight;
    nearBottom.current = box.scrollHeight - box.scrollTop - box.clientHeight < 64;
    setAtBottom(nearBottom.current); if (nearBottom.current) setNewCount(0);
  }, [messages, roomLoading, active, selected, auth]);
  useEffect(() => {
    if (!active || !selected) return;
    const timer = setTimeout(markVisible, 160);
    window.addEventListener("focus", markVisible);
    return () => { clearTimeout(timer); window.removeEventListener("focus", markVisible); };
  }, [active, selected, messages, room, composing, markVisible]);
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (input) { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 124)}px`; }
  }, [draft, selected, active]);
  useEffect(() => {
    if (!active || !auth) return;
    const id = selectedRef.current;
    setDraft(drafts.current[id] || ""); setFiles(attachments.current[id] || []);
    if (roomRef.current) inputRef.current?.focus({ preventScroll: true });
  }, [active, auth]);
  useEffect(() => {
    if (active && !roomLoading && room && focusRoom.current) { focusRoom.current = false; inputRef.current?.focus({ preventScroll: true }); }
  }, [active, roomLoading, room]);
  useEffect(() => {
    if (!emojiOpen) return;
    const onPointer = event => { if (!emojiRef.current?.contains(event.target)) setEmojiOpen(false); };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [emojiOpen]);

  const onDraft = value => { const id = selectedRef.current; drafts.current[id] = value; setDraft(value); persist(id); };
  const jumpToBottom = () => { const box = scrollRef.current; if (box) box.scrollTop = box.scrollHeight; nearBottom.current = true; setAtBottom(true); setNewCount(0); markVisible(); };
  const onScroll = () => {
    const box = scrollRef.current; if (!box) return;
    nearBottom.current = box.scrollHeight - box.scrollTop - box.clientHeight < 64;
    setAtBottom(nearBottom.current); if (nearBottom.current) setNewCount(0);
    markVisible();
  };
  const openCompose = () => {
    setComposing(true); setPeopleQuery(""); setError("");
    requestAnimationFrame(() => rootRef.current?.querySelector(".chat-compose .chat-search input")?.focus({ preventScroll: true }));
    api("/api/chat/people").then(data => { if (aliveRef.current) setPeople(data.items || []); }).catch(e => setError(chatError(e)));
  };
  const beginDirect = async id => {
    if (!auth || groupBusy) return;
    setGroupBusy(true); setError("");
    try { const data = await api("/api/chat/direct", json(auth.csrf, { userId: Number(id) })); chooseRoom(data.id); refreshList().catch(() => {}); }
    catch (e) { setError(chatError(e)); } finally { setGroupBusy(false); }
  };
  const createGroup = async () => {
    if (!auth || groupBusy || chosen.length < 2) return;
    setGroupBusy(true); setError("");
    const fingerprint = JSON.stringify([groupTitle, [...chosen].sort((a, b) => a - b)]);
    if (groupKey.current?.fingerprint !== fingerprint) groupKey.current = { fingerprint, id: uid() };
    try {
      const data = await api("/api/chat/groups", json(auth.csrf, { memberIds: chosen, title: groupTitle, requestId: groupKey.current.id }));
      groupKey.current = null; setChosen([]); setGroupTitle(""); chooseRoom(data.id); refreshList().catch(() => {});
    } catch (e) { setError(chatError(e)); } finally { setGroupBusy(false); }
  };

  const pickFiles = list => {
    const id = selectedRef.current, entries = [...list];
    if (!id || !entries.length || sendingLock.current.has(id)) return;
    pickCounts.current.set(id, (pickCounts.current.get(id) || 0) + 1);
    attachingLock.current.add(id); setAttachingRooms(new Set(attachingLock.current));
    pickQueue.current = pickQueue.current.catch(() => {}).then(async () => {
      for (const file of entries) {
        try {
          const info = prepareFile(file, attachments.current[id] || []);
          const data = await new Promise((resolve, reject) => {
            const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
            reader.onerror = () => reject(new Error(`Não foi possível abrir ${info.name}.`)); reader.readAsDataURL(file);
          });
          attachments.current[id] = [...(attachments.current[id] || []), { ...info, data, key: uid() }];
          if (selectedRef.current === id && aliveRef.current) setFiles(attachments.current[id]);
        } catch (e) { if (selectedRef.current === id && aliveRef.current) setError(chatError(e)); }
      }
    }).finally(() => {
      const remaining = (pickCounts.current.get(id) || 1) - 1;
      if (remaining) pickCounts.current.set(id, remaining);
      else { pickCounts.current.delete(id); attachingLock.current.delete(id); }
      if (aliveRef.current) setAttachingRooms(new Set(attachingLock.current));
    });
  };
  const removeFile = key => {
    const id = selectedRef.current; attachments.current[id] = (attachments.current[id] || []).filter(file => file.key !== key); setFiles(attachments.current[id]);
  };
  const submit = async event => {
    event?.preventDefault();
    const id = selectedRef.current, text = drafts.current[id] || "", attached = attachments.current[id] || [];
    if (!id || !auth || !roomRef.current || sendingLock.current.has(id) || attachingLock.current.has(id) || (!text.trim() && !attached.length)) return;
    const fingerprint = JSON.stringify([text, attached.map(file => file.key)]);
    if (pendingSends.current.get(id)?.fingerprint !== fingerprint) pendingSends.current.set(id, { fingerprint, key: uid() });
    sendingLock.current.add(id); setSendingRooms(new Set(sendingLock.current)); setError(""); setEmojiOpen(false);
    try {
      await api(`/api/chat/conversations/${encodeURIComponent(id)}/messages`, json(auth.csrf, { body: text, files: attached.map(({ name, type, data }) => ({ name, type, data })), clientMessageId: pendingSends.current.get(id).key }));
      pendingSends.current.delete(id);
      if (drafts.current[id] === text) drafts.current[id] = "";
      const sentKeys = new Set(attached.map(file => file.key));
      attachments.current[id] = (attachments.current[id] || []).filter(file => !sentKeys.has(file.key));
      persist(selectedRef.current);
      if (selectedRef.current === id && aliveRef.current) { setDraft(drafts.current[id] || ""); setFiles(attachments.current[id]); setAnnouncement("Mensagem enviada."); }
      // A failed history refresh after a successful POST must never restore a sent draft.
      const since = selectedRef.current === id ? messagesRef.current.at(-1)?.id || 0 : 0;
      try {
        const data = await api(`/api/chat/conversations/${encodeURIComponent(id)}/messages?since=${since}&limit=100`);
        appendMessages(id, data, true);
      } catch { if (aliveRef.current) setConnection("reconnecting"); }
      refreshList().catch(() => {});
    } catch (e) { if (selectedRef.current === id && aliveRef.current) setError(chatError(e)); }
    finally {
      sendingLock.current.delete(id); if (aliveRef.current) { setSendingRooms(new Set(sendingLock.current)); if (selectedRef.current === id && activeRef.current) inputRef.current?.focus(); }
    }
  };
  const insertEmoji = value => {
    const input = inputRef.current, start = input?.selectionStart ?? draft.length, end = input?.selectionEnd ?? draft.length;
    const next = draft.slice(0, start) + value + draft.slice(end);
    if (next.length <= MAX_BODY) onDraft(next);
    setEmojiOpen(false); requestAnimationFrame(() => { input?.focus(); input?.setSelectionRange(start + value.length, start + value.length); });
  };

  const selectedItem = items.find(item => item.id === selected), group = room?.conversation?.kind === "GROUP" || selectedItem?.kind === "GROUP";
  const other = room?.members?.find(member => member.id !== auth?.userId);
  const roomTitle = group ? room?.conversation?.title || selectedItem?.title || room?.members?.filter(member => member.id !== auth?.userId).map(member => member.name).join(", ") || "Grupo" : other?.name || selectedItem?.title || "Conversa";
  const avatarProps = { group, avatarId: other?.avatarId || selectedItem?.avatarId, moodId: other?.moodId || selectedItem?.moodId, name: roomTitle };
  const visibleItems = unreadOnly ? items.filter(item => item.unread > 0) : items;
  const visiblePeople = people.filter(person => normalize(person.name).includes(normalize(peopleQuery)));
  const sending = sendingRooms.has(selected), attaching = attachingRooms.has(selected);
  const firstUnread = messages.find(message => message.id > readBoundary && message.senderId !== auth?.userId)?.id;
  const subtitle = selected ? group ? `${room?.members?.length || ""} participantes`.trim() : "Conversa individual" : "Sua equipe, mais perto";

  return <section ref={rootRef} className={`chat-workspace${compact ? " chat-compact" : ""}${expanded ? " chat-expanded" : ""}${selected ? " has-room" : ""}${composing ? " is-choosing" : ""}`} aria-label={compact ? "Mensagens da equipe" : "Chat da equipe"}>
    {compact && <header className="chat-header">
      {selected || composing ? <IconButton icon="back" label={composing ? "Voltar às conversas" : "Voltar à lista de conversas"} onClick={() => composing ? setComposing(false) : chooseRoom("")}/> : <span className="chat-header-symbol"><ChatIcon/></span>}
      {selected && !composing && <ConversationAvatar {...avatarProps} size={36}/>}
      <div className="chat-heading"><strong>{composing ? "Nova conversa" : selected ? roomTitle : "Chat da equipe"}</strong><small>{composing ? "Escolha com quem conversar" : subtitle}</small></div>
      <div className="chat-header-actions"><IconButton icon={expanded ? "shrink" : "expand"} label={expanded ? "Reduzir janela do chat" : "Ampliar janela do chat"} onClick={onExpand}/><FullChatLink className="chat-icon-button" href={`/central-dp/chat${selected ? `?id=${encodeURIComponent(selected)}` : ""}`} aria-label="Abrir chat completo" title="Abrir chat completo" onClick={() => { persist(); onClose?.(); }}><ChatIcon name="external" size={18}/></FullChatLink><IconButton icon="minus" label="Minimizar chat" onClick={onClose}/></div>
    </header>}
    {error && <div className="chat-error" role="alert"><span>{error}{/sessão|entre novamente/i.test(error) && <> <a href="/entrar">Entrar novamente</a></>}</span><div>{!auth ? <button type="button" onClick={() => window.location.reload()}>Recarregar</button> : selected && !room ? <button type="button" onClick={() => loadRoom(selected)}>Tentar novamente</button> : <button type="button" onClick={() => { setError(""); synchronize(); }}>Atualizar</button>}<IconButton icon="close" label="Dispensar aviso do chat" onClick={() => setError("")}/></div></div>}
    <div className="chat-body">
      <aside className="chat-list-pane" aria-label="Conversas da equipe">
        {composing ? <section className="chat-compose" aria-label="Nova conversa">
          <header><div><strong>Nova conversa</strong><small>Individual ou com um grupo da equipe</small></div><IconButton icon="close" label="Cancelar nova conversa" onClick={() => setComposing(false)}/></header>
          <div className="chat-compose-tabs" role="group" aria-label="Tipo de conversa"><button type="button" aria-pressed={composeKind === "DIRECT"} onClick={() => setComposeKind("DIRECT")}>Individual</button><button type="button" aria-pressed={composeKind === "GROUP"} onClick={() => setComposeKind("GROUP")}>Grupo</button></div>
          {composeKind === "GROUP" && <label className="chat-group-title">Nome do grupo (opcional)<input value={groupTitle} onChange={event => setGroupTitle(event.target.value)} maxLength={80} placeholder="Ex.: Equipe de admissões"/></label>}
          <label className="chat-search"><ChatIcon name="search" size={17}/><input value={peopleQuery} onChange={event => setPeopleQuery(event.target.value)} placeholder="Buscar pessoa" aria-label="Buscar pessoa para conversar"/></label>
          <div className="chat-people-list">{visiblePeople.map(person => composeKind === "DIRECT" ? <button type="button" key={person.id} disabled={groupBusy} className="chat-person" onClick={() => beginDirect(person.id)}><ConversationAvatar avatarId={person.avatarId} moodId={person.moodId} name={person.name} size={40}/><strong>{person.name}</strong><ChatIcon name="plus" size={17}/></button> : <label key={person.id} className="chat-person"><ConversationAvatar avatarId={person.avatarId} moodId={person.moodId} name={person.name} size={36}/><span>{person.name}</span><input type="checkbox" checked={chosen.includes(person.id)} onChange={event => setChosen(current => event.target.checked ? [...current, person.id] : current.filter(id => id !== person.id))}/></label>)}{!visiblePeople.length && <p className="chat-state">{people.length ? "Nenhuma pessoa corresponde à busca." : "Não há outras contas ativas para iniciar uma conversa."}</p>}</div>
          {composeKind === "GROUP" && <footer><small>{chosen.length} selecionada{chosen.length !== 1 ? "s" : ""} · mínimo de 2</small><button className="chat-primary-button" type="button" disabled={chosen.length < 2 || groupBusy} onClick={createGroup}>{groupBusy ? "Criando…" : "Criar grupo"}</button></footer>}
        </section> : <>
          <div className="chat-list-top"><div className="chat-list-heading"><div><h2>Conversas</h2><small>{unread ? `${unread} mensagem${unread > 1 ? "s" : ""} não lida${unread > 1 ? "s" : ""}` : "Suas conversas com a equipe"}</small></div><IconButton icon="plus" label="Nova conversa" onClick={openCompose} disabled={!auth}/></div><label className="chat-search"><ChatIcon name="search" size={17}/><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Buscar conversa ou mensagem" aria-label="Pesquisar no chat"/>{query && <IconButton icon="close" label="Limpar busca do chat" onClick={() => setQuery("")}/>}</label><div className="chat-list-filters" role="group" aria-label="Filtrar conversas"><button type="button" aria-pressed={!unreadOnly} onClick={() => setUnreadOnly(false)}>Todas</button><button type="button" aria-pressed={unreadOnly} onClick={() => setUnreadOnly(true)}>Não lidas{unread > 0 && <span>{unread}</span>}</button></div></div>
          <div className="chat-conversations" aria-busy={loading}>{loading ? <div className="chat-list-skeleton" aria-label="Carregando conversas">{[1, 2, 3].map(id => <span key={id}/>)}</div> : visibleItems.length ? visibleItems.map(item => <button type="button" key={item.id} className={`chat-conversation${selected === item.id ? " active" : ""}${item.unread ? " is-unread" : ""}`} aria-current={selected === item.id ? "true" : undefined} onClick={() => chooseRoom(item.id)}><ConversationAvatar group={item.kind === "GROUP"} avatarId={item.avatarId} moodId={item.moodId} name={item.title}/><span className="chat-conversation-copy"><strong>{item.title}</strong><small>{drafts.current[item.id] ? <><em>Rascunho: </em>{drafts.current[item.id]}</> : item.lastBody ? `${item.lastAuthor ? item.lastAuthor.split(" ")[0] + ": " : ""}${item.lastBody}` : "Envie a primeira mensagem"}</small></span><span className="chat-conversation-meta"><time title={fullStamp(item.updatedAt)}>{conversationTime(item.updatedAt)}</time>{item.unread > 0 && <b aria-label={`${item.unread} mensagens não lidas`}>{item.unread > 99 ? "99+" : item.unread}</b>}</span></button>) : <div className="chat-state"><span className="chat-state-symbol"><ChatIcon name={query ? "search" : "chat"} size={26}/></span><strong>{query ? "Nenhum resultado" : unreadOnly ? "Tudo em dia" : "Sua primeira conversa começa aqui"}</strong><span>{query ? "Tente outro nome ou trecho da mensagem." : unreadOnly ? "Você não tem conversas não lidas." : "Escolha uma pessoa da equipe para conversar."}</span>{!query && !unreadOnly && <button className="chat-primary-button" type="button" onClick={openCompose} disabled={!auth}>Nova conversa</button>}</div>}</div>
          <footer className={`chat-connection${connection === "reconnecting" ? " is-reconnecting" : ""}`} role="status"><span/>{connection === "connecting" ? "Conectando ao chat…" : connection === "reconnecting" ? "Reconectando… suas mensagens estão preservadas" : "Chat atualizado"}{connection === "reconnecting" && <button type="button" onClick={synchronize}>Tentar</button>}</footer>
        </>}
      </aside>
      <div className={`chat-room${!selected ? " chat-room-empty" : ""}`} aria-label={selected ? `Conversa com ${roomTitle}` : "Área da conversa"}>
        {!selected ? <div className="chat-welcome"><span className="chat-welcome-icon"><ChatIcon size={36}/><i><ChatIcon name="people" size={18}/></i></span><span className="chat-welcome-kicker">CHAT DA EQUIPE</span><h2>Uma conversa facilita o dia.</h2><p>Alinhe demandas, compartilhe arquivos e acompanhe as respostas em um só lugar.</p><button className="chat-primary-button" type="button" onClick={openCompose} disabled={!auth}><ChatIcon name="plus" size={17}/>Nova conversa</button><small>Selecione uma conversa ao lado para continuar.</small></div> : <>
          {!compact && <header className="chat-room-subhead"><IconButton icon="back" label="Voltar à lista de conversas" onClick={() => chooseRoom("")}/><ConversationAvatar {...avatarProps} size={42}/><div className="chat-heading"><strong>{roomTitle}</strong><small>{subtitle}</small></div></header>}
          {group && <details className="chat-members-details"><summary><ChatIcon name="people" size={15}/><span>Participantes</span><ChatIcon name="down" size={14}/></summary><div>{room?.members?.map(member => <span key={member.id}><ConversationAvatar avatarId={member.avatarId} moodId={member.moodId} name={member.name} size={27}/>{member.id === auth?.userId ? "Você" : member.name}</span>)}</div></details>}
          <div className="chat-timeline">
            <div className="chat-messages" ref={scrollRef} onScroll={onScroll} role="log" aria-label="Mensagens da conversa" aria-live="polite" aria-relevant="additions" aria-busy={roomLoading}>
              {roomLoading ? <div className="chat-messages-loading" role="status"><span className="chat-spinner"/>Abrindo conversa…</div> : !room ? <div className="chat-state"><strong>Não foi possível abrir esta conversa.</strong><button className="chat-primary-button" type="button" onClick={() => loadRoom(selected)}>Tentar novamente</button></div> : <>
                {room.hasOlder && <div className="chat-older"><button type="button" disabled={olderLoading} onClick={() => loadRoom(selected, true)}>{olderLoading ? "Carregando…" : "Carregar mensagens anteriores"}</button>{room.unreadBeforePage && <small>Há mensagens não lidas antes deste trecho.</small>}</div>}
                {messages.length ? messages.map((message, index) => {
                  const mine = message.senderId === auth?.userId, previous = messages[index - 1];
                  const newDay = !previous || dayKey(previous.createdAt) !== dayKey(message.createdAt);
                  const grouped = previous?.senderId === message.senderId && !newDay && message.id !== firstUnread;
                  return <Fragment key={message.id}>{newDay && <div className="chat-day"><span>{dayLabel(message.createdAt)}</span></div>}{message.id === firstUnread && <div className="chat-unread-divider"><span>Mensagens não lidas</span></div>}<article data-message-id={message.id} className={`chat-message${mine ? " mine" : ""}${grouped ? " is-grouped" : ""}`}>
                    {!mine && !grouped && <span className="chat-message-avatar"><AvatarPortrait avatarId={room.members?.find(member => member.id === message.senderId)?.avatarId || message.avatarId} expression={room.members?.find(member => member.id === message.senderId)?.moodId || message.moodId} size={28} title={`Avatar de ${message.senderName}`}/></span>}
                    <div className="chat-bubble">{!mine && group && !grouped && <strong className="chat-message-author">{message.senderName}</strong>}{message.body && <MessageText text={message.body}/ >}{message.attachments?.map(attachment => <a key={attachment.id} className="chat-attachment" href={attachment.url} target="_blank" rel="noreferrer" title={`Abrir ${attachment.filename}`}><ChatIcon name="file" size={23}/><span><strong>{attachment.filename}</strong><small>{fileSize(attachment.size_bytes)} · Abrir arquivo</small></span><ChatIcon name="external" size={14}/></a>)}<footer><time title={fullStamp(message.createdAt)}>{messageTime(message.createdAt)}</time>{mine && <span title="Mensagem enviada"><ChatIcon name="check" size={13}/><span className="visually-hidden">Enviada</span></span>}</footer></div>
                  </article></Fragment>;
                }) : <div className="chat-state chat-first-message"><span className="chat-state-symbol"><ChatIcon size={25}/></span><strong>Dê o primeiro olá</strong><span>As mensagens desta conversa aparecerão aqui.</span></div>}
              </>}
            </div>
            {!roomLoading && !atBottom && <button type="button" className="chat-jump" onClick={jumpToBottom}><ChatIcon name="down" size={16}/>{newCount ? `${newCount} nova${newCount > 1 ? "s" : ""} mensagem${newCount > 1 ? "s" : ""}` : "Ir para o fim"}</button>}
          </div>
          <form className={`chat-composer${dragging ? " is-dragging" : ""}`} onSubmit={submit} onDragEnter={event => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); dragDepth.current++; setDragging(true); } }} onDragOver={event => { if (event.dataTransfer.types.includes("Files")) event.preventDefault(); }} onDragLeave={() => { dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragging(false); }} onDrop={event => { event.preventDefault(); dragDepth.current = 0; setDragging(false); if (!sending) pickFiles(event.dataTransfer.files); }}>
            {dragging && <div className="chat-drop-hint"><ChatIcon name="clip"/>Solte os arquivos para anexar</div>}
            {files.length > 0 && <div className="chat-pending-files" aria-label="Arquivos para enviar">{files.map(file => <span key={file.key}><ChatIcon name="file" size={18}/><span><strong>{file.name}</strong><small>{fileSize(file.size)}</small></span><IconButton icon="close" label={`Remover ${file.name}`} disabled={sending} onClick={() => removeFile(file.key)}/></span>)}</div>}
            <div className="chat-input-wrap"><textarea ref={inputRef} value={draft} onChange={event => onDraft(event.target.value)} onPaste={event => { const pasted = [...event.clipboardData.items].filter(item => item.kind === "file").map(item => item.getAsFile()).filter(Boolean); if (pasted.length) { event.preventDefault(); pickFiles(pasted); } }} onKeyDown={event => { if (event.key === "Escape" && emojiOpen) { event.preventDefault(); setEmojiOpen(false); } if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); submit(); } }} placeholder={roomLoading ? "Abrindo conversa…" : "Escreva uma mensagem…"} rows={1} maxLength={MAX_BODY} aria-label="Mensagem" disabled={!room || sending}/>
              <div className="chat-composer-actions"><div className="chat-emoji-wrap" ref={emojiRef}><IconButton icon="smile" label="Inserir emoji" aria-expanded={emojiOpen} onClick={() => setEmojiOpen(value => !value)} disabled={!room || sending}/>{emojiOpen && <div className="chat-emoji-picker" role="group" aria-label="Escolher emoji">{emoji.map(value => <button type="button" key={value} onClick={() => insertEmoji(value)} aria-label={`Inserir ${value}`}>{value}</button>)}</div>}</div><IconButton icon="clip" label="Anexar arquivos" onClick={() => fileInputRef.current?.click()} disabled={!room || sending || attaching}/><input ref={fileInputRef} type="file" multiple hidden onChange={event => { pickFiles(event.target.files); event.target.value = ""; }} accept={FILE_ACCEPT}/><span className="chat-input-hint">{attaching ? "Preparando anexos…" : sending ? "Enviando…" : draft.length > 4500 ? `${draft.length}/${MAX_BODY}` : "Enter para enviar"}</span><button type="submit" className="chat-send" aria-label={sending ? "Enviando mensagem" : "Enviar mensagem"} title="Enviar mensagem" disabled={!room || sending || attaching || (!draft.trim() && !files.length)}>{sending ? <span className="chat-spinner"/> : <ChatIcon name="send" size={18}/>}</button></div>
            </div><div className="chat-composer-footnote"><span>Shift + Enter para nova linha</span><span>Anexos até 10 MB cada</span></div>
          </form>
        </>}
      </div>
    </div><span className="visually-hidden" role="status">{announcement}</span>
  </section>;
}
