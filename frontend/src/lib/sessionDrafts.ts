export type SessionDrafts = Readonly<Record<string, string>>

export function draftFor(drafts: SessionDrafts, sessionId: string): string {
  return sessionId ? drafts[sessionId] ?? '' : ''
}

export function withDraft(
  drafts: SessionDrafts,
  sessionId: string,
  value: string,
): SessionDrafts {
  if (!sessionId) return drafts
  if (drafts[sessionId] === value) return drafts
  const next = { ...drafts }
  if (value) next[sessionId] = value
  else delete next[sessionId]
  return next
}
