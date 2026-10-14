# Addendum 2: the first attempt was stopped, and the harness was given a resume

Written 2026-10-13, after the first start of the ReadableSTEM live run and before any run of it
finished. `PREREGISTRATION.md` and Addendum 1 stand unchanged.

## What happened

The first attempt (code commit `501b5a2865fa1e5fda1c45006ffb51287f0bf7a6`, the one Addendum
1 froze) was started at 14:26 local time as a background job of the tool the integrator works
in. That job type is stopped after two hours. At 15:01 the run was still inside its first
document run, 481 model calls and 255 second-check calls in (about 35 minutes), at roughly 19
calls a minute while another job on the shared machine used 8 cores. At that pace the four
tasks with two repeats need roughly ten hours, so the job would have been killed with nothing
saved. The integrator stopped it by hand at 15:02. **No run had finished; no output of it was
opened, scored or used.** Its spend is not recorded anywhere (the process was killed before it
wrote a bundle); at list price it is an estimated $1 to $2, outside the $12 cap of the
second attempt.

## What changed in the harness (commit `e9b5979f043b56b1bbb7b2f3742fd87a769e7e51`)

- `--resume`: a recording interrupted for any reason continues from the `.partial` folder.
  A run (task, repeat) whose answers, report and run record were written is read back and its
  spend counted against the cap; the run that was in flight is made again whole.
- A run's files are now written only when the run finished (before, the files of a run the cap
  stopped were left in the partial folder).
- The recording is started detached from the tool's job limit.
- `Makefile` targets and licence notes for the recorded runs (docs only).

`git diff 501b5a2..e9b5979 --stat` touches `proof/{bundle,runner,engine,document}.py`,
`cli/proof.py`, one test file, `Makefile` and `THIRD_PARTY_NOTICES.md`. No prompt, model,
threshold, rule, scorer or setting changed (the pipeline, `eval/` and `models/` are untouched),
so this is a harness change that alters no system and no score (registration section 10.4).
The code that makes the numbers is commit `e9b5979f...`; Addendum 1's other freezes (prompts,
fingerprint of the invented-content code, parser plan, locks) hold as they were.

## Time

Because of the pace above, the integrator expects the ReadableSTEM recording alone to take
hours. If it cannot finish every planned run, the report says so on top (the `--keep-finished`
banner) and the missing runs are not replaced by anything.
