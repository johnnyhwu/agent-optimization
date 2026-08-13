"""Optimize (Stage 3): training a skill the way a network is trained.

The algorithm is [SkillOpt](https://github.com/microsoft/SkillOpt) — the skill
document is the trainable parameter, and it is optimised with epochs, a batch
size, a learning rate and a validation gate. What lives here is the seam between
that algorithm and this platform:

    rollout   -> ours   (app/pipeline.call_agent + the LLM judge)
    reflect   -> theirs (vendor/reflect.py, the analyst prompts)
    aggregate -> theirs (vendor/aggregate.py)
    select    -> theirs (vendor/clip.py + vendor/scheduler.py)
    update    -> theirs (vendor/skill.py, forked for a skill *directory*)
    gate      -> theirs (vendor/gate.py, verbatim)

`vendor/` is upstream code, pinned and diffable — see VENDORED.md for the commit
and for every line that differs. Everything outside `vendor/` is ours.
"""
