// What to tell an owner about the skill tags they have just typed on a question.
//
// The tags are not decoration: an optimization run groups a set's questions by
// skill, and `optimizer/dataset.py` keeps only the questions carrying *exactly
// one* — everything else goes to the `ambiguous` bucket, which no skill is
// optimized from. So the two cases worth a sentence are the two that quietly
// remove a question from the work: none, and more than one.
//
// One tag is the ordinary case and gets no note. A field that comments on
// correct input trains people to stop reading it.
export function skillNote(skills) {
  const n = (skills || []).length;
  if (n === 1) return null;
  if (n === 0) {
    return "No skill tag. An optimization run works from groups of questions that " +
      "share one skill, and this question would not be in any of them.";
  }
  return `Tagged with ${n} skills. An optimization run only groups questions ` +
    `carrying exactly one, so this question would not be in any of them.`;
}
