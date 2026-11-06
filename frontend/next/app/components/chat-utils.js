export const MAX_BODY = 5000;
export const MAX_FILE_BYTES = 10 * 1024 * 1024;
export const MAX_TOTAL_BYTES = 20 * 1024 * 1024;
export const FILE_ACCEPT = ".pdf,.txt,.csv,.png,.jpg,.jpeg,.gif,.webp,.zip,.docx,.xlsx,.pptx";
export const uid = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
const draftSessions = new Map();
// Attachments stay in memory while switching between the bubble and full chat.
export function chatDraftSession(userId, saved = {}) {
  if (!draftSessions.has(userId)) draftSessions.set(userId, { text: saved, files: {}, sending: new Set(), attaching: new Set(), pending: new Map() });
  return draftSessions.get(userId);
}
export function chatDate(value) {
  if (!value) return null;
  const date = new Date(`${value}${/Z|[+-]\d\d:\d\d$/.test(value) ? "" : "Z"}`);
  return Number.isNaN(date.getTime()) ? null : date;
}
export const messageTime = value => chatDate(value)?.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) || "";
export const fullStamp = value => chatDate(value)?.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) || "";
export function dayKey(value) {
  const date = chatDate(value);
  return date ? `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}` : "";
}
export function dayLabel(value, now = new Date()) {
  const date = chatDate(value);
  if (!date) return "";
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(midnight); yesterday.setDate(yesterday.getDate() - 1);
  if (date >= midnight) return "Hoje";
  if (date >= yesterday) return "Ontem";
  return date.toLocaleDateString("pt-BR", { day: "2-digit", month: "long", ...(date.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) });
}
export function conversationTime(value) {
  const date = chatDate(value);
  if (!date) return "";
  return dayKey(value) === dayKey(new Date().toISOString()) ? messageTime(value) : date.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
}
export const fileSize = bytes => bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MB` : `${Math.max(1, Math.ceil(bytes / 1024))} KB`;
export function mergeMessages(current, incoming) {
  const map = new Map(current.map(message => [message.id, message]));
  for (const message of incoming || []) map.set(message.id, message);
  return [...map.values()].sort((a, b) => a.id - b.id);
}
// A read receipt may advance only through messages actually seen in this room.
export function readableCursor(messages, seen, cursor, userId, unreadBeforePage = false) {
  if (unreadBeforePage) return cursor;
  let next = cursor;
  for (const message of messages) {
    if (message.id <= next) continue;
    if (message.senderId !== userId && !seen.has(message.id)) break;
    next = message.id;
  }
  return next;
}
const guessedTypes = { pdf: "application/pdf", txt: "text/plain", csv: "text/csv", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", zip: "application/zip", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation" };
export function prepareFile(file, existing) {
  const name = file.name || (file.type === "image/jpeg" ? "imagem-colada.jpg" : "imagem-colada.png");
  const extension = name.split(".").at(-1)?.toLowerCase();
  if (!guessedTypes[extension]) throw new Error(`${name}: formato não permitido.`);
  if (!file.size) throw new Error(`${name}: o arquivo está vazio.`);
  if (file.size > MAX_FILE_BYTES) throw new Error(`${name}: máximo de 10 MB por arquivo.`);
  if (existing.length >= 10) throw new Error("Você pode anexar até 10 arquivos por mensagem.");
  if (existing.reduce((sum, item) => sum + item.size, 0) + file.size > MAX_TOTAL_BYTES) throw new Error("Os anexos desta mensagem podem somar até 20 MB.");
  return { name, type: guessedTypes[extension], size: file.size };
}
export async function chatApi(url, options = {}) {
  const signals = [AbortSignal.timeout(15000), ...(options.signal ? [options.signal] : [])];
  const response = await fetch(url, { credentials: "same-origin", cache: "no-store", ...options, signal: AbortSignal.any(signals) });
  const data = await response.json().catch(() => ({ error: "O servidor não retornou uma resposta válida." }));
  if (!response.ok) { const error = new Error(data.error || "Não foi possível concluir. Tente novamente."); error.status = response.status; throw error; }
  return data;
}
export const chatJson = (csrf, value) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf, ...value }) });
export const chatError = error => error.name === "TimeoutError" ? "A conexão demorou a responder. Tente novamente." : error.message || "Sem conexão com o chat. Tente novamente.";
