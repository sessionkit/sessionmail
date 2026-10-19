---
name: returned-not-dead
title: A pager that returned is not a failure; the words are returned and reset
---

When `pager listen` comes back because a mail landed, that is the thing
working. No verb, exit code, help line or ride-along line may call it
"dead" or "failed".

- The ride-along printed `ARM DEAD - re-arm now` on every legitimate
  delivery for days; a bug that did not exist was reported.
- Vocabulary for a return: **fired**, **returned**, **heard**.
- Vocabulary for arming again: **reset** ("the pager needs to be reset").
- Two pids under one session are an arm and its waiter, not a duplicate.
- Classify every return before reacting: a `Subject:` in its output is a
  delivery; "nothing added" is a no-op.

Test: grep every string the pager can print for `dead|died|failed` - no
hits.
