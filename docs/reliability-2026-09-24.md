# Reliability passes — 2026-09-24

**Status: measured.** Three audits, run in this checkout on 2026-09-24 (UTC
`04:00`, in the same session that produced the 2026-09-23 headline run — that
run's own header already reads `2026-09-24T03:38:27Z`, because the local date
rolled over mid-session). Same corpus version, same three gates, same machine.
Each audit answers a question `docs/results-2026-09-23.md` left open. Where a
line says *not established*, it was not measured.

Nothing in the gate layer, the decoder or the corpus was changed by any of
these. Every finding below is recorded as a finding; the ones that would need a
code change are named as such and left open.

```bash
python3 measurements/reliability_cross_process.py    # A
python3 measurements/matcher_scope_audit.py          # B
python3 measurements/channel_confusion_audit.py      # C
```

---

## A — Cross-process nondeterminism

`run_matrix` repeats each (gate, case) `n` times **inside one process, back to
back**. That catches a gate that flips call to call. It cannot catch a gate
whose answer depends on state outliving a harness process — a cache file, a
lockfile, a daemon, a counter under `~/.cache` — because all `n` repeats share
that state.

`measurements/reliability_cross_process.py` runs `hyperreal run` as **three
separate OS processes**, each at n=2, and diffs the per-(gate, case) verdicts
across them. Separate processes mean separate interpreter state, separate
subprocess trees, separate session ids and a gap in wall-clock time.

**576 gate invocations, 3 passes × 192 run records.**

| | |
|---|---|
| verdict agreement | **96 of 96** (gate, case) pairs answered identically in every pass |
| within-pass disagreement | 0 pairs |
| channel agreement | every pair used the same channel in every pass |

That is not proof of determinism. It is **no variance observed over three
consecutive passes on one machine on one day** — absence is not permission
(`CLAUDE.md` 8.0 #4). A gate with a per-boot or per-day behaviour change is
invisible to this probe, and so is anything depending on state this harness
never created.

### A1 — Latency does not travel, and this is the third time it has said so

| gate | pass 0 | pass 1 | pass 2 |
|---|---|---|---|
| `ecc-pre-bash` | p50 70 / p95 80 | p50 74 / p95 88 | p50 74 / p95 88 |
| `hookify` | p50 30 / p95 35 | p50 30 / p95 34 | p50 28 / p95 34 |

Tight here. It has not always been: `docs/results-2026-09-23.md` R2 recorded
p95s of 507 ms and 1533 ms on two of these same rows, R4 recorded 30158 ms on a
third, and `docs/architecture.md` S9 saw 19 ms → 431 ms on the scaffold night.
**Verdicts have been stable across every measurement this repo has made;
latency has not been stable across any of them.** A published latency figure
names its run or it says nothing.

---

## B — Matcher-scope audit

Guard 1 decides per case whether a gate is `APPLICABLE`, and that one decision
moves a case into a denominator or out of one. It is made by
`re.fullmatch(matcher, tool_name)`. Both halves of that are worth auditing.

### B1 — One matcher of three is Hyperreal's, not the author's

| gate | matcher | origin |
|---|---|---|
| `validate-write` | `Write\|Edit\|MultiEdit` | **Hyperreal's reading** — a shipped example, registered in no `hooks.json` |
| `hookify` | `*` | the plugin's own `hooks.json` |
| `ecc-pre-bash` | `Bash` | ecc's own `hooks.json` |

The audit detects this **structurally**, from whether the registration's
`source` names a `hooks.json` entry — not by reading the note text, because a
reworded note is a note a prose-matching check silently passes (8.0 #5, and the
same rule `report.invariance` follows).

### B2 — Three (gate, tool) pairs change scope under a different regex reading

`applies_to`'s semantics are Hyperreal's choice and are **UNVERIFIED against a
running Claude Code binary**. Under the three plausible readings a host could
use — anchored `fullmatch` (ours), unanchored `search`, case-insensitive
`fullmatch` — these disagree:

| gate | tool | fullmatch (ours) | search | fullmatch -i |
|---|---|---|---|---|
| `ecc-pre-bash` | `BashOutput` | no | **yes** | no |
| `validate-write` | `NotebookEdit` | no | **yes** | no |
| `validate-write` | `TodoWrite` | no | **yes** | no |

**`validate-write` is the sharp one.** Its matcher is an unanchored alternation,
so `Write` is a substring of `TodoWrite` and `Edit` of `NotebookEdit`. If the
host searches rather than anchors, that gate is shown every to-do write in the
session — and Hyperreal would score those cases `NOT_APPLICABLE`, a false
exclusion invented by the harness. This is the mirror image of the false catch
Guard 1 was built to stop, and the current corpus cannot see it because of B3.

**Open, not fixed.** Resolving it needs a measurement against a running host,
which this repo has not made (`docs/protocol.md`, Unverified). Changing
`applies_to` on a guess would replace an unverified reading with a different
unverified reading.

### B3 — A one-tool corpus cannot exercise matcher scope at all

All 32 cases are `Bash`, so `validate-write` is applicable to **0 of 32** and
scores `0 of 0`. A scope fact that reads like a quality one. Already in
`docs/results-2026-09-23.md` R1; repeated here because this is the audit where
it is measurable rather than noticed.

---

## C — Channel-confusion audit

`protocol.decode` reads four channels in a fixed precedence — stdout JSON,
stderr JSON, exit 2, `continue: false`. Precedence is a **resolution**, and a
resolution hides a conflict. The decoder notes exactly two shapes and resolves
everything else in silence. This audit re-reads the raw channels of every call
and reports each conflict, applying the shipped precedence unchanged.

**128 calls (2 gates × 32 cases × n=2; `validate-write` skipped on 32 cases as
out of scope).**

| # | check | result |
|---|---|---|
| C1 | two channels each carrying a permission decision | **0** — the stdout-before-stderr precedence was never load-bearing in this run |
| C2 | a decision alongside an exit code the decoder does not read | **0** — every deciding call exited 0 or 2 |
| C3 | exit-vs-JSON disagreement the decoder *does* note | **64** — all `ecc-pre-bash`: exit 0 with `permissionDecision: deny` |
| C4 | a `continue: false` that lost to a `permissionDecision` | **0** |
| C5 | more than one top-level JSON object on one stream | **0** |
| C6 | Guard 2 (echoed input) firing | **0** |
| C7 | gate repeats our envelope back at all | **0 of 128** |

C3 is the known one and it is not new: 64 of 64 `ecc-pre-bash` calls ship
`exit 0` + `deny`, and what a live agent does with that combination is still
unverified. The conformance note fires on every one of them and stays.

### C7 — a finding: Guard 2 has no live exercise on this corpus

`gates/registry.py`'s Guard 2 docstring records, as measured, that "every ecc
hook invoked through `run-with-flags.js` echoed our hook JSON verbatim". The
audit tests that claim directly and **structurally**: it looks for the
per-call `session_id` Guard 3 minted, a string no gate can produce except by
copying our envelope.

**Neither registered gate echoed it, on any of 128 calls.** `ecc-pre-bash`
emits its decision object and nothing else. So either the hook this harness
scores is not one of the hooks that echoed, or that behaviour has changed.

What follows: Guard 2 is currently held up **only** by
`tests/test_registry.py`, and its docstring's measured claim is broader than
anything reproducible here. A guard with no live exercise and a guard that is
dead look identical from outside (8.0 #3), which is why this is written down
rather than left as a zero in a table.

**Not patched.** This run was not allowed to change gate or decoder semantics,
and the docstring's wording is part of the gate layer's own record. Two things
are open: narrow the Guard 2 docstring to the hooks it was actually measured on,
and give Guard 2 a live exercise — a deliberately echoing fixture gate in the
registered set, not only in the unit tests.

---

## What these three passes did not establish

- **Determinism.** Three consecutive passes on one machine. See A.
- **The host's matcher semantics.** Still unverified. See B2.
- **Which channel a running Claude Code reads, or in what order.** Unchanged
  from `docs/protocol.md`.
- **Anything about `validate-write`.** Out of scope on every case; not scored,
  not accused.
- **That Guard 2 works.** It did not fire. See C7.
