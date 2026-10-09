/**
 * Add a dictated transcript to the end of the message box text.
 * Joins with one space (none if the text already ends with whitespace) and
 * keeps the transcript's capitalisation, since names like "Atomic Habits" matter.
 */
export function appendTranscript(existing, addition) {
  const add = addition.trim();
  if (!add) return existing;
  if (!existing.trim()) return add;
  return /\s$/.test(existing) ? existing + add : `${existing} ${add}`;
}
