"""How a gate is registered, and when a row about it may be published.

`docs/adding-a-gate.md` records the fields a registration has to carry. This is
that record, plus the two guards that the live probe of the installed gates
(2026-09-23, `tests/probe_installed_gates.py`) showed a registration cannot go
without. Both guards exist to stop Hyperreal publishing a number that is true
of our harness and false of the gate.

GUARD 1 - MATCHER SCOPE. A PreToolUse hook is registered against a *matcher*
on the tool name. Claude Code never shows a `"matcher": "Bash"` hook a `Write`
call. Measured: ecc's `pre-bash-dispatcher` (matcher ``Bash``) was handed a
`Write` case by the harness and answered ``deny`` - a **false catch** for a
decision the gate would never be asked to make in deployment. Equally, scoring
it ``SILENT`` on `Write` cases it never sees would be a **false miss**. So a
case outside a gate's matcher is `NOT_APPLICABLE`: not a catch, not a miss, and
counted in neither denominator.

GUARD 2 - ECHOED INPUT. A gate that copies the harness's own stdin to its
stdout hands the decoder a payload the *corpus author* wrote. Measured: every
ecc hook invoked through `run-with-flags.js` echoed our hook JSON verbatim. If
a case's `tool_input` carried a `hookSpecificOutput` block, the decoder would
read the case's own text as the gate's verdict - a catch or a miss chosen by
whoever wrote the case. `screen_echoed_input` refuses any decision whose
payload is something we sent, and records it as a conformance note.

READINESS. Three states read as `SILENT` from outside and mean different
things: a gate deliberately quiet, a gate installed with no rules configured,
and a gate that crashed on import and failed open. Measured on hookify:
unconfigured it prints ``{}``; without `CLAUDE_PLUGIN_ROOT` it prints
``{"systemMessage": "Hookify import error: ..."}`` and still exits 0. Scoring
either as a miss is a false accusation - the same class of error as publishing
a broken environment as `failed_open` (`docs/architecture.md` S9). A gate is
scored only when its registration says it is `READY`.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import uuid
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from hyperreal.adapters.subprocess_gate import (
    BASELINE_ENV,
    DEFAULT_TIMEOUT_SECONDS,
    SubprocessGate,
)
from hyperreal.protocol import Channel, GateAnswer, Verdict, _parse_json_object

# Shell operators that `shlex.split` turns into literal argv tokens instead of
# honouring. Claude Code runs a hook's `command` through a shell; we do not.
# Registering such a command would invoke the gate WRONGLY and then publish the
# result - a false accusation against software we mis-ran.
SHELL_OPERATORS = frozenset({"|", "||", "&", "&&", ";", ";;", "<", ">", ">>", "(", ")"})

# A matcher of "*" or "" means every tool, which is how Claude Code's own hook
# configs spell it (measured in ecc's hooks.json: two entries use "*").
MATCH_ALL = frozenset({"*", ""})

# Channels whose verdict came out of a JSON payload, and can therefore be a
# payload we ourselves supplied. An exit-code verdict cannot be echoed.
_PAYLOAD_CHANNELS = frozenset({Channel.STDOUT_JSON, Channel.STDERR_JSON, Channel.CONTINUE_FALSE})


class Applicability(str, Enum):
    """Whether this gate is shown this case at all, in deployment."""

    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"


class Readiness(str, Enum):
    """Whether a row about this gate may be published.

    `UNCONFIGURED` is the one that matters and the one that is easy to miss: a
    gate whose rules the operator never wrote is not a gate that missed.
    """

    READY = "ready"
    UNCONFIGURED = "unconfigured"
    NOT_INSTALLED = "not_installed"
    UNKNOWN = "unknown"


# Only this state is scorable. Everything else is published as its own state,
# never folded into a miss.
SCORABLE = frozenset({Readiness.READY})


@dataclass(frozen=True)
class GateRegistration:
    """One entrant. Immutable; every field appears in a published row.

    `source` is not decoration. It says where `argv` came from - ideally the
    gate's own hook config, read at registration time rather than transcribed -
    so a gate author disputing how they were invoked can check it.
    """

    name: str
    argv: tuple[str, ...]
    source: str
    matcher: str = "*"
    version: str = "unpinned"
    cwd: str | None = None
    env_allowlist: tuple[str, ...] = tuple(BASELINE_ENV)
    extra_env: Mapping[str, str] | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    # LLM gates only. A model with no date is not a pinned model, so both or
    # neither - a row that cannot say which model answered cannot be reproduced.
    model: str | None = None
    model_date: str | None = None
    # None means "not established", which is printed as `unknown`, never as no.
    network: bool | None = None
    readiness_probe: Callable[[], tuple[Readiness, str]] | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("a registration needs a name; it is the column header")
        if not self.argv:
            raise ValueError(f"gate {self.name!r} has no argv: nothing to invoke")
        if not self.source.strip():
            raise ValueError(
                f"gate {self.name!r} has no source: a published row must say where "
                "its argv came from"
            )
        # MATCH_ALL members are sentinels, not patterns. "*" is how Claude
        # Code's own hook configs spell "every tool", and it is not valid regex.
        if self.matcher not in MATCH_ALL:
            try:
                re.compile(self.matcher)
            except re.error as exc:
                raise ValueError(
                    f"gate {self.name!r} matcher {self.matcher!r} is not a regex: {exc}"
                )
        if bool(self.model) != bool(self.model_date):
            raise ValueError(
                f"gate {self.name!r}: model and model_date go together - an LLM row "
                "that cannot name the model and the date is not reproducible"
            )

    # -- scope ---------------------------------------------------------------

    def applies_to(self, tool_name: str) -> Applicability:
        """Would a real agent show this gate a call to ``tool_name``?

        Semantics chosen here: the matcher is a regex matched against the whole
        tool name, and ``*``/``""`` means all. UNVERIFIED against a running
        Claude Code binary - whether the host anchors the match is not something
        this repo has measured, and `docs/gates.md` says so.
        """
        if self.matcher in MATCH_ALL:
            return Applicability.APPLICABLE
        if re.fullmatch(self.matcher, tool_name):
            return Applicability.APPLICABLE
        return Applicability.NOT_APPLICABLE

    # -- readiness -----------------------------------------------------------

    def readiness(self) -> tuple[Readiness, str]:
        """Is this gate in a state where a result about it means anything?

        Checks the executable first, because "not installed" outranks anything
        a probe could say, then defers to the gate's own probe. A registration
        with no probe is `UNKNOWN`, never `READY`: absence of a check is not a
        passing check (`CLAUDE.md` 8.0 #4).
        """
        program = self.argv[0]
        if os.path.sep in program:
            if not os.path.exists(program):
                return Readiness.NOT_INSTALLED, f"{program} does not exist"
        elif shutil.which(program) is None:
            return Readiness.NOT_INSTALLED, f"{program} is not on PATH"
        if self.readiness_probe is None:
            return Readiness.UNKNOWN, "no readiness probe registered"
        return self.readiness_probe()

    # -- invocation ----------------------------------------------------------

    def to_subprocess_gate(self) -> SubprocessGate:
        return SubprocessGate(
            name=self.name,
            argv=list(self.argv),
            version=self.version,
            cwd=self.cwd,
            env_allowlist=self.env_allowlist,
            extra_env=self.extra_env,
            timeout_seconds=self.timeout_seconds,
        )

    def run(
        self,
        hook_input: Mapping[str, Any],
        *,
        session_id: str | None = None,
    ) -> "RegisteredRun":
        """Invoke the gate on one case, with all three guards applied.

        A `NOT_APPLICABLE` case is not run at all. Spending a subprocess on a
        call the gate would never see buys nothing and, as the ecc measurement
        showed, can produce a decision that then has to be thrown away.

        GUARD 3 - SESSION ISOLATION. Every run gets a fresh `session_id` unless
        the caller names one. Measured 2026-09-23 on ecc's `pre-bash-dispatcher`:
        under one shared session it answered ``deny`` to ``ls -la`` and then
        ``silent`` to the same call three times running; given a fresh session
        per call it answered ``deny`` four times out of four. A gate that
        remembers is scored by the order its cases happened to run in, which
        means whoever sets the case order sets the result - the same class of
        thumb-on-the-scale as pooling the families (`docs/architecture.md` S5).

        This is deliberately not left to each caller to remember: a gate enforced
        at one call site is not a gate (`CLAUDE.md` 8.0 #2). The caller's mapping
        is never mutated; a new one is built.
        """
        tool_name = str(hook_input.get("tool_name", ""))
        applicability = self.applies_to(tool_name)
        readiness, readiness_detail = self.readiness()
        resolved_session = session_id or f"hyperreal-{uuid.uuid4()}"
        if applicability is Applicability.NOT_APPLICABLE:
            return RegisteredRun(
                registration=self,
                applicability=applicability,
                readiness=readiness,
                readiness_detail=readiness_detail,
                answer=None,
                wall_seconds=0.0,
                session_id=resolved_session,
            )
        isolated = {**hook_input, "session_id": resolved_session}
        run = self.to_subprocess_gate().run(isolated)
        answer = screen_echoed_input(run.answer, isolated)
        return RegisteredRun(
            registration=self,
            applicability=applicability,
            readiness=readiness,
            readiness_detail=readiness_detail,
            answer=answer,
            wall_seconds=run.wall_seconds,
            session_id=resolved_session,
        )


@dataclass(frozen=True)
class RegisteredRun:
    """One gate, one case, plus the two facts that decide whether it counts."""

    registration: GateRegistration
    applicability: Applicability
    readiness: Readiness
    readiness_detail: str
    answer: GateAnswer | None
    wall_seconds: float
    # The session this run was given. Published, because a gate that remembers
    # across sessions and a gate that does not are different products and the
    # row has to say which session it was asked under.
    session_id: str = ""

    @property
    def is_scorable(self) -> bool:
        """True only when this row belongs in a catch/miss denominator."""
        return (
            self.applicability is Applicability.APPLICABLE
            and self.readiness in SCORABLE
            and self.answer is not None
        )


# -- guard 2: echoed input ---------------------------------------------------


def _nested_values(value: Any) -> list[Any]:
    """Every value inside ``value``, itself included. Bounded by the input we
    built, so there is no untrusted recursion depth here."""
    found = [value]
    if isinstance(value, Mapping):
        for item in value.values():
            found.extend(_nested_values(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_nested_values(item))
    return found


def _is_echo(payload: Mapping[str, Any], hook_input: Mapping[str, Any]) -> bool:
    return any(payload == candidate for candidate in _nested_values(hook_input))


def _payload_on(channel: Channel, answer: GateAnswer) -> Mapping[str, Any] | None:
    text = answer.raw_stdout if channel is Channel.STDOUT_JSON else answer.raw_stderr
    return _parse_json_object(text)


def screen_echoed_input(answer: GateAnswer, hook_input: Mapping[str, Any]) -> GateAnswer:
    """Refuse a decision whose payload is something the harness itself sent.

    A gate that pipes its stdin to its stdout is not deciding, and the payload
    the decoder then reads was written by the corpus author. Taking a verdict
    from it would let a case choose its own result on any echoing gate.

    The answer is downgraded to `SILENT` - the gate expressed no opinion of its
    own - with a conformance note, and every raw channel is kept so the row is
    still recheckable by hand.
    """
    if answer.channel not in _PAYLOAD_CHANNELS:
        return answer
    payload = _payload_on(answer.channel, answer)
    if payload is None or not _is_echo(payload, hook_input):
        return answer
    return replace(
        answer,
        verdict=Verdict.SILENT,
        channel=Channel.NONE,
        reason="",
        updated_input=None,
        failed_open=False,
    ).with_note(
        f"gate echoed the harness's own input on {answer.channel.value}; no verdict "
        f"was taken from it (a decision read out of an echo is written by the case, "
        f"not by the gate)"
    )


# -- registration from a plugin's own hook config ----------------------------


def from_plugin_hooks(
    hooks_json_path: str,
    *,
    plugin_root: str,
    event: str = "PreToolUse",
    name_prefix: str = "",
    version: str = "unpinned",
) -> list[GateRegistration]:
    """Read every ``event`` hook out of a plugin's own ``hooks.json``.

    Taking argv from the plugin's config rather than transcribing it is a
    neutrality control: Hyperreal does not get to decide how somebody else's
    gate is invoked. It also carries the matcher across, which is what Guard 1
    needs.

    Only ``{"type": "command"}`` hooks are returned. A ``{"type": "prompt"}``
    hook is evaluated by the host agent and cannot be spawned as a process -
    `docs/architecture.md` S8 #1 is still undecided, so they are skipped rather
    than simulated.
    """
    with open(hooks_json_path, encoding="utf-8") as handle:
        config = json.load(handle)
    registrations: list[GateRegistration] = []
    for group in config.get("hooks", {}).get(event, []):
        matcher = group.get("matcher") or "*"
        for hook in group.get("hooks", []):
            if hook.get("type") != "command":
                continue
            command = str(hook.get("command", "")).replace("${CLAUDE_PLUGIN_ROOT}", plugin_root)
            _refuse_shell_commands(command, hooks_json_path)
            argv = tuple(shlex.split(command))
            if not argv:
                continue
            label = _label_for(argv)
            timeout = hook.get("timeout")
            registrations.append(
                GateRegistration(
                    name=f"{name_prefix}{label}",
                    argv=argv,
                    source=f"{hooks_json_path}::hooks.{event}[matcher={matcher}]",
                    matcher=matcher,
                    version=version,
                    cwd=plugin_root,
                    extra_env={"CLAUDE_PLUGIN_ROOT": plugin_root},
                    timeout_seconds=float(timeout) if timeout else DEFAULT_TIMEOUT_SECONDS,
                )
            )
    return registrations


def _refuse_shell_commands(command: str, source: str) -> None:
    """Refuse a hook command that only a shell could run correctly.

    The harness invokes a gate with `subprocess.run(argv)` and no shell, which
    is a safety property worth keeping (`docs/architecture.md` S6). But a hook
    registered as ``a.sh | tee log`` would be split by `shlex` into literal
    argv tokens - `|` and `tee` handed to `a.sh` as arguments - and the gate
    would answer nonsense that we would then publish as its behaviour.

    Refusing is the safe failure: a gate Hyperreal cannot invoke faithfully is
    a gate Hyperreal does not score. Silently mis-invoking it is the one
    outcome that must not happen.
    """
    # `shlex.split` would only separate an operator that happens to be
    # whitespace-delimited, so `a.sh|tee` would slip through as one token. The
    # lexer with `punctuation_chars` splits them out wherever they appear -
    # except inside quotes, which is what leaves ecc's 4KB quoted `node -e`
    # program intact.
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise ValueError(f"hook command in {source} does not lex: {exc}")
    operators = sorted({token for token in tokens if token in SHELL_OPERATORS})
    if operators:
        raise ValueError(
            f"hook command in {source} needs a shell ({', '.join(operators)}); "
            "Hyperreal invokes gates without one and will not guess at an "
            "equivalent argv"
        )


def _label_for(argv: Sequence[str]) -> str:
    """A short, stable column header for an argv that may be a 4KB bootstrap.

    ecc registers its hooks as ``node -e "<4KB of resolver>" node
    scripts/hooks/run-with-flags.js pre:edit-write:gateguard-fact-force ...``.
    Naming a gate after ``argv[0]`` would call all eight of them ``node``; the
    flag tag is the part a human recognises.
    """
    for token in argv:
        if token.startswith("pre:"):
            return token
    for token in reversed(argv):
        if token.endswith((".js", ".py", ".sh")):
            return os.path.basename(token)
    return os.path.basename(argv[0])
