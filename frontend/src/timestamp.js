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
