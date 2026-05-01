"""Interactive terminal chat wiring."""

from __future__ import annotations

import json
from pathlib import Path

from medlens.agent import MedicationSafetyAgent
from medlens.agent_loop import run_agent_turn
from medlens.chat.commands import handle_command
from medlens.chat.renderer import ChatRenderer
from medlens.chat.session import ChatSession
from medlens.tools.local_safety import MedicationSafetyStore
from medlens.tools.registry import dispatch


def run_terminal_chat(
    *,
    store: MedicationSafetyStore,
    agent: MedicationSafetyAgent,
    session: ChatSession,
    initial_medications: tuple[str, ...] = (),
    initial_question: str | None = None,
    effect_limit: int = 5,
    debug_trace_path: Path | None = None,
) -> int:
    renderer = ChatRenderer()
    renderer.welcome(session, (store.normalization_db, store.evidence_db))

    if initial_medications:
        dispatch("add_medications", {"names": list(initial_medications)}, store=store, session=session)
        renderer.medications(session)
    if initial_question:
        _answer(
            initial_question,
            store=store,
            agent=agent,
            session=session,
            renderer=renderer,
            effect_limit=effect_limit,
            debug_trace_path=debug_trace_path,
        )

    try:
        while True:
            message = _prompt("medlens> ").strip()
            if not message:
                continue
            if message.startswith("/"):
                result = handle_command(message, store=store, session=session)
                if result.should_exit:
                    return 0
                if result.kind == "report" and session.last_report is not None:
                    renderer.report(session.last_report)
                elif result.kind == "medications":
                    renderer.medications(session)
                elif result.kind == "trace":
                    renderer.trace(session.last_trace)
                elif result.kind == "error":
                    renderer.error(str(result.payload))
                else:
                    renderer.result(result.kind.title(), result.payload)
                continue
            _answer(
                message,
                store=store,
                agent=agent,
                session=session,
                renderer=renderer,
                effect_limit=effect_limit,
                debug_trace_path=debug_trace_path,
            )
    except KeyboardInterrupt:
        print()
        return 130
    except EOFError:
        print()
        return 0
    except RuntimeError as exc:
        renderer.error(f"MedLens agent error: {exc}")
        return 1


def _answer(
    message: str,
    *,
    store: MedicationSafetyStore,
    agent: MedicationSafetyAgent,
    session: ChatSession,
    renderer: ChatRenderer,
    effect_limit: int,
    debug_trace_path: Path | None,
) -> None:
    session.clear_turn_trace()
    result = run_agent_turn(
        provider=agent.provider,
        session=session,
        store=store,
        user_message=message,
        effect_limit=effect_limit,
    )
    _append_debug_trace(debug_trace_path, result.to_debug_dict())
    if session.medications:
        renderer.medications(session)
    renderer.assistant(
        result.final_text,
        report=result.report,
        show_grounding=not _is_clarification_text(result.final_text) and not (result.used_tools and result.report is None),
    )


def _prompt(message: str) -> str:
    try:
        from prompt_toolkit import prompt
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.patch_stdout import patch_stdout
    except ModuleNotFoundError:
        return input(message)

    completer = WordCompleter(["/meds", "/add", "/remove", "/check", "/report", "/why", "/sources", "/trace", "/clear", "/provider", "/help", "/quit"])
    with patch_stdout():
        return prompt(message, completer=completer)


def _append_debug_trace(path: Path | None, payload: dict[str, object]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str))
        handle.write("\n")


def _is_clarification_text(value: str) -> bool:
    lowered = value.casefold()
    return "could not confidently match" in lowered or "still need the exact medicine name" in lowered
