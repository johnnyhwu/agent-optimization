// `YYYY/MM/DD HH:mm`, padded, in the reader's own timezone.
//
// Written out rather than handed to `toLocaleString`, which renders as
// "2026/8/14 下午9:31:02" on one machine and "8/14/2026, 9:31:02 PM" on another.
// Both of those were being shown as the *primary* label of a run in the list;
// as a subtitle under a name, what matters instead is that a column of them is
// the same width and can be compared down the page.
//
// Seconds are dropped deliberately. Nothing here is decided by them, and they
// were the widest part of the locale form.
export function shortStamp(value) {
  if (!value) return "";
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${at.getFullYear()}/${pad(at.getMonth() + 1)}/${pad(at.getDate())}`
    + ` ${pad(at.getHours())}:${pad(at.getMinutes())}`
  );
}

/**
 * "40s ago" / "12m ago" / "5h ago", then the absolute stamp.
 *
 * This lived inside AttemptList and stopped at an hour, falling through to
 * `toLocaleTimeString()` — which reintroduced, in one list, all three problems
 * the comment at the top of this file describes. The playground's attempt list
 * read "25s ago", "20m ago", "40m ago", "9:19:22 AM", "8:59:22 AM": two
 * different notations in one column, the second of them locale-dependent, with
 * seconds nobody needs and *no date at all*, so an attempt from last Tuesday
 * was indistinguishable from one an hour ago.
 *
 * The ladder now runs to days before handing over to `shortStamp`, so the
 * absolute form is the same one every other timestamp in the product uses.
 *
 * `now` is injectable so this is testable without freezing the clock.
 */
export function relativeStamp(value, now = Date.now()) {
  if (!value) return "";
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "";
  const seconds = Math.max(0, (now - at.getTime()) / 1000);
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  // Up to a day stays relative: within a working day "5h ago" is the answer to
  // the question being asked, and a clock time is not.
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return shortStamp(value);
}
