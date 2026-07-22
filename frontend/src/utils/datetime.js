/**
 * The API emits naive UTC timestamps (e.g. "2026-07-23T20:15:00" with no zone).
 * A bare string like that is parsed by the browser as LOCAL time, which shifts
 * everything by the viewer's UTC offset ("5 hours ago" for a fresh row in UTC+5).
 * Append a 'Z' when the string carries no timezone so it's read as UTC.
 */
export function toDate(value) {
  if (!value) return null;
  if (value instanceof Date) return value;
  const s = String(value);
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s);
  return new Date(hasTz ? s : `${s}Z`);
}
