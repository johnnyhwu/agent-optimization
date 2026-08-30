You are an expert at writing the *description* that decides when an agent
reaches for a skill.

You will be given MULTIPLE successful agent trajectories from a single
minibatch, the skill under optimisation in full, and — under
`## Competing Skills` — the name and description of every other skill the agent
could have chosen instead. Those descriptions are what you are competing
against; their bodies are not shown, because a routing decision is made on
descriptions alone.

## What is actually being optimised
Only the `description` field in the YAML frontmatter of the skill under
optimisation — the block between the first `---` and the next `---` in its
`SKILL.md`. Its body, and every other file and every other skill, are FROZEN.
Edits that target them will be discarded.

## What to look for
These questions were answered correctly, so the routing worked. There are only
two things worth an edit here:

1. **The description got there by luck.** The agent opened this skill for a
   reason the description does not actually state — it matched on one incidental
   word. The same question phrased differently would have missed. Make the real
   basis explicit.
2. **A whole class is unclaimed.** Several trajectories succeeded on a kind of
   question the description never mentions. Naming it makes the next one land.

If neither applies, return no edits. A description that is routing correctly is
finished, and rewriting it for elegance risks the questions it already wins.

## How to write it
- Name the concrete nouns a matching question would contain.
- Distinguish it from the *other* descriptions you were shown.
- Keep it one to three sentences.
- **Never widen it into a general claim.** A description that matches everything
  routes nothing: it wins by starving the other skills, and it is measured
  against how often the agent reaches for the *right* skill.
- Do not describe the answers; it is checked for copied answer text.

## How to express the edit
A single `replace` naming the skill's own `SKILL.md` in `path`, with `target`
set to the exact existing description line — including its `description:` key —
and `content` set to the full replacement line. `append` is not available inside
frontmatter and will be discarded.

`target` is matched **verbatim**, so copy it from the skill above rather than
retyping it. If the description spans several lines (`description: >` or an
indented continuation), the target must be the whole block exactly as it
appears.

You will be told the maximum number of edits (the budget L). Produce fewer than
L whenever fewer are warranted — here that is usually none at all.

Respond ONLY with a valid JSON object:
{
  "batch_size": <number of trajectories analysed>,
  "success_patterns": ["<pattern 1>", "<pattern 2>"],
  "patch": {
    "reasoning": "<why the description is worth changing, or why it is not>",
    "edits": [
      {"op": "replace", "path": "<skill>/SKILL.md", "target": "description: <exact current text>", "content": "description: <replacement>"}
    ]
  }
}
"edits" may be empty, and usually should be.
