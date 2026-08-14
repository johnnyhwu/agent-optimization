// "3 questions", "1 question".
//
// The codebase had both spellings: `${n} run${n === 1 ? "" : "s"}` written out
// where someone cared, and `${n} question(s)` where they did not. The second is
// the tell that nobody read the sentence back — it appears mid-prose in a
// warning about validation not being held out, which is a sentence a developer
// is meant to stop and think about.
//
// Irregulars are passed explicitly rather than guessed. There is no English
// pluraliser worth shipping for a vocabulary of a dozen nouns.
export function plural(n, one, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

// The count without the number, for sentences that already said it.
export function pluralise(n, one, many = `${one}s`) {
  return n === 1 ? one : many;
}
