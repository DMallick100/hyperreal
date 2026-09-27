# The bridge arm did not run, and the reason is a measurement

**Date:** 2026-09-27
**Asked for:** the N2.3 bridge arm — `claude-haiku` through the shim host, full
30-case matrix, `--max-output-tokens 8192`, isolated cwd fresh per case, same
harness and gates as the other shim arms, $2 spend ceiling.
**Delivered:** the arm is **BLOCKED and did not run**. The release gate stays
shut. **Total spend: $0.0000.** The ceiling never came near binding, because
nothing billable ever executed.

This document is the evidence for that sentence, plus the three harness defects
the attempt exposed — all three now fixed and gated by tests.

---

## 1. The headline

> **No `anthropic/*` model is reachable from this machine.** The only provider
> credential here is the Vercel AI gateway key, and that account is on the free
> tier, which restricts Anthropic models specifically. `openai/gpt-5` on the same
> key, in the same minute, answers 200 and bills.

So the bridge arm — whose entire premise is *the baseline's own model, through the
shim* — has no route. This is not a defect we can fix by trying harder, and it is
not a result about the shim. It is an absent capability, reported as absent.

**What stays true as a consequence:** `combination_rule:
"first_deny_wins (shim assumption, unverified)"` keeps its qualifier on every
shim row, and **no non-Anthropic table may be published** (spec N2.3), because
the number that must be printed above it does not exist.

## 2. What was measured, and how

### 2.1 The arm was launched properly before it was declared blocked

Three smoke runs, one case each (`destructive-overwrite`, the same case the gpt-5
and open-CN smokes used), through `measurements/live_shim_sweep.py --arm bridge`
at `--max-output-tokens 8192`:

| tag | interpreter | result | spend |
|---|---|---|---|
| `smoke-maxtok8192` | `/usr/local/bin/python3` | `harness_error` / CERTIFICATE_VERIFY_FAILED ×3 | $0 |
| `smoke2-maxtok8192` | BOMTrace venv | `harness_error` / `http 403` ×3 | $0 |
| `smoke3-maxtok8192` | BOMTrace venv, after the fixes | `harness_error` / `http 403` ×**1**, cause in the row | $0 |

The first was **ours**: `hyperreal/trust.py` falls back to the system trust store
when `certifi` is not importable and *says so*, and on this machine `certifi`
exists only inside the BOMTrace venv
(`/Users/dhruvmallick/AeroTrace/bomtrace/backend/venv/bin/python`) — which is the
interpreter every previously published arm ran under, recorded in
`results/provider-preflight-2026-09-26b.json`'s `trust_store` field and nowhere
in an arm's own file. See §3.3.

### 2.2 The vendor sweep: is it one id, a generation, or the vendor?

Those three have different consequences — re-pin and run today; run a *different*
model wearing the bridge's name; or no bridge at all — so it was measured rather
than inferred. Every `anthropic/*` id priced in the stored catalogue got one
16-token call, paced 3s (`results/bridge-vendor-access-2026-09-27.json`):

| answer | ids | what it means |
|---|---|---|
| **403 `no_providers_available`** / `RestrictedModelsError` | **15** | "Free tier users do not have access to this model." A measurement. |
| **429 `rate_limit_exceeded`** | **2** | Message: *"No access to this model at this time."* See §3.1. |
| **500 `AI_APICallError`** | **1** (`anthropic/claude-3-haiku`) | The provider's weather. **UNMEASURED**, not restricted. |
| **answered** | **0** | — |

The three non-403 answers were re-run three attempts each at 20s spacing
(`results/bridge-vendor-access-2026-09-27-holes.json`): both 429s repeated all
three times with the same no-access sentence, and the 500 repeated all three
times. **So: 17 of 18 measured as no-access, 1 unmeasured, 0 answered.**

Stated precisely, because the difference matters: *17 of 18* is the number.
"18 of 18 restricted" would put a harness error in a denominator, which spec N5.1
forbids and which this repo has already had to unpick once.

### 2.3 There is no other route

`scripts_hyperreal_bridge_routes.py`, presence-only, no key material printed:

- `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`,
  `OPENROUTER_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`,
  `GOOGLE_APPLICATION_CREDENTIALS` — **all ABSENT** from the environment.
- `~/.zshrc` — 0 mentions of an Anthropic or OpenRouter key; 1 of the gateway key.
- `~/.vai/config.json` — one key, `api_key`, the gateway's. The only credential
  on the machine.
- `~/.config/anthropic/`, `~/.anthropic/` — absent.

**OpenRouter is still absent**, so spec §N0.2's *"OpenRouter preferred if it
works"* is re-confirmed unsatisfiable on this machine, and the gateway remains
the provider **for want of an alternative, not on merit** — now with a measured
consequence, since the alternative it lacks is the one that would serve this arm.

`claude` IS on PATH, and it cannot serve this arm: it is the **real host**, which
is the baseline's own route. Running the baseline's model back through the real
host would compare the real host with itself.

## 3. The three harness defects the attempt found — fixed, and gated

All three sit on the path between "the arm failed" and "a human can tell why",
which is the path this repo cares about most.

### 3.1 `http 403` was the entire record of a refused model

The row's `error_detail`, the arm summary's `harness_errors[].detail`, and the
135-byte per-case transcript all carried the status and nothing else — while
`ProviderReply.raw` held the provider's sentence at the moment of failure and
dropped it on the way out. **Diagnosing a 403 therefore required a second script
that re-posted to the provider to read a body the harness already had.**

Fixed: `shim_providers._error_message_of()` extracts the provider's own sentence
(bounded to 300 chars); `transport_detail` becomes
`http 403 'no_providers_available': Free tier users do not have access…`; and the
per-case transcript gained `transport_detail` + `provider_error_code`, empty on a
healthy turn.

**The message is EVIDENCE, not a classifier input.** `_error_code_of`'s docstring
— "the `message` is never read" — still binds every decision, per `CLAUDE.md` 8.A
(detect state structurally, never by substring-matching prose). This session is
exactly why both halves are needed: **two ids answer 429 `rate_limit_exceeded`
with the message "No access to this model at this time."** Every machine-readable
field on those responses says *throttle*; the truth is *entitlement*. Nothing
branches on that sentence — and a reader can now see it, which is the only
correct response to a machine field that is wrong.

### 3.2 A 403 bought three attempts and 80s of backoff

`provider_http` is a transport bucket holding a 429 and a 403 alike, and
`RETRYABLE_STAGES` contains it — so an *authorization answer* was re-asked twice
with 20s and 60s waits. Across a 30-case arm that is **90 attempts and ~41
minutes spent re-asking a question already answered.**

Fixed: `NON_RETRYABLE_STATUSES = {401, 403, 404}`, checked in the retry
predicate. Status-based and therefore structural — no 403 improves in 20 seconds.
**429 and 500 remain retryable**, including the two 429s known to be access
denials: a throttle genuinely does clear, and the only thing that distinguishes
those two is prose.

Measured effect (`smoke3`): 3 attempts → **1**, 82s → instant, $0 either way.

### 3.3 An arm could not say which trust store it verified against

`hyperreal/trust.py`'s own docstring says a caller that cannot name its store
"cannot defend a reachability result in either direction" — and
`provider_preflight.py` records it while the **arm did not**. So the first smoke's
`CERTIFICATE_VERIFY_FAILED` read as an unreachable provider when it was an
interpreter without `certifi`.

Fixed: the arm payload now carries `trust_store` and `interpreter`.

### 3.4 (bonus) The sweep could not express the cap its own arms published at

Four of the five published shim arms ran at 8192, and `live_shim_sweep.py` had no
`--max-output-tokens` — so every raised-cap arm had to be launched by a
hand-written wrapper, and **the documented entry point could not reproduce its own
results.** Fixed: `--max-output-tokens` and `--http-timeout` are passed through
explicitly and recorded on the sweep summary, with defaults **imported** from
`shim_providers` rather than retyped (the probe's own `--http-timeout` had
retyped `120`; it now reads `DEFAULT_HTTP_TIMEOUT`).

## 4. What it cost

| item | spend |
|---|---|
| 3 bridge smokes (1 case each) | $0.0000 |
| 18-id vendor access sweep | $0.0000 |
| 3-id hole re-probe (9 calls) | $0.0000 |
| **total** | **$0.0000** |

A refused request bills nothing, so the whole investigation was free. The $2
ceiling was passed to the runner as `--arm-budget-usd` on every launch and never
approached.

## 5. Status of the release gate

**SHUT.** Unchanged by this session except that its reason is now measured and
recorded in three places a future session will actually hit: the sweep's `ARMS`
table, `live_shim_bridge.py`'s missing-file error, and this document.

Two ways to open it, both the operator's call:

1. **Put paid credits on the Vercel AI gateway account.** The 403 body names this
   itself. It would make the pinned `anthropic/claude-haiku-4.5` run unchanged —
   `live_shim_sweep.py --arm bridge --tag r1-maxtok8192 --max-output-tokens 8192
   --arm-budget-usd 2` is the whole command, and on gpt-5's measured rates the arm
   should land near **$0.10–$0.20** at haiku's $1/$5 per Mtok. Not done here:
   spending money on an account upgrade is not a thing a session decides.
2. **Supply an `OPENROUTER_API_KEY`** (spec §N0.2's preferred provider, absent
   since the fourth delivery) **or an `ANTHROPIC_API_KEY`.** The first needs no
   code — `resolve_provider("openrouter")` already exists. The second would need
   an Anthropic-shaped provider in `shim_providers`, which is a **second
   transport** for one arm and therefore a comparability question the operator
   should answer before anyone builds it: the other four arms all reached their
   models through one OpenAI-compatible endpoint.

**What does NOT change either way**, and is worth restating because it is the
limit no credit buys off: `live-fresh-haiku-iso-2026-09-25.json` records
`model: "haiku"`, the alias, and its stream logs have been reaped from /tmp. So
even a fully-run bridge arm can answer *"did the command run?"* and can never
claim **model parity** with the published baseline.

## 6. Suite

`203` unittest tests green across **13** modules, named explicitly — `discover -s
tests` fails here (no `__init__.py`) and `discover -s .` prints "Ran 0 tests …
OK", a dead gate. 185 before this session, +18 in the new
`tests/test_shim_access_denied.py`.
