# -*- coding: utf-8 -*-
"""ReAct Agent engine for Abaqus AI automation."""

import json
from typing import Any, Callable, Dict, List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from abaqus_agent.agent.llm import LLMClient
from abaqus_agent.agent.prompts import ABAQUS_SYSTEM_PROMPT
from abaqus_agent.config import MAX_AGENT_STEPS
from abaqus_agent.tools.registry import OPENAI_TOOLS_SCHEMA, dispatch_tool_call
from abaqus_agent.tools.doc_search import doc_searcher


class AbaqusAgent:
    """ReAct autonomous agent driving Abaqus CAE via tool calls."""

    def __init__(self, llm_client: Optional[LLMClient] = None, console: Optional[Console] = None):
        self.llm = llm_client or LLMClient()
        self.console = console or Console()
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": ABAQUS_SYSTEM_PROMPT}
        ]
        self._last_tool_sig: Optional[str] = None
        self._repeat_call_count: int = 0

    def reset(self) -> None:
        """Reset conversation history back to initial system prompt."""
        self.messages = [
            {"role": "system", "content": ABAQUS_SYSTEM_PROMPT}
        ]
        self._last_tool_sig = None
        self._repeat_call_count = 0

    def run_turn(self, user_prompt: str) -> str:
        """Execute a full multi-step conversation turn with the user."""
        # Refresh versioned reference each turn rather than accumulating stale
        # cards from unrelated earlier operations. Tool history remains intact.
        self.messages = [message for message in self.messages
                         if message.get("name") != "abaqus_runtime_reference"]
        self.messages.append({"role": "user", "content": user_prompt})
        self.messages.append({"role": "system", "name": "abaqus_runtime_reference",
                              "content": doc_searcher.runtime_context(user_prompt)})
        self._last_tool_sig = None
        self._repeat_call_count = 0

        step_count = 0
        final_response_text = ""

        while step_count < MAX_AGENT_STEPS:
            step_count += 1

            # Stream response from LLM with live thinking display
            token_gen, result_container = self.llm.chat_stream(self.messages, tools=OPENAI_TOOLS_SCHEMA)

            reasoning_chunks: List[str] = []
            streamed_content: List[str] = []

            with self.console.status("[dim cyan]Thinking and analyzing...[/dim cyan]", spinner="dots") as status:
                for token_type, delta in token_gen:
                    if token_type == "reasoning":
                        reasoning_chunks.append(delta)
                        last_line = "".join(reasoning_chunks).strip().splitlines()[-1] if reasoning_chunks else ""
                        if len(last_line) > 50:
                            last_line = last_line[-50:]
                        status.update(f"[dim cyan]Thinking: {last_line}[/dim cyan]")
                    elif token_type == "content":
                        streamed_content.append(delta)

            # Render reasoning thoughts if present
            if reasoning_chunks:
                full_thought = "".join(reasoning_chunks).strip()
                self.console.print(Panel(
                    f"[dim]{full_thought}[/dim]",
                    title="[dim]Thought Process[/dim]",
                    border_style="dim",
                    padding=(0, 1)
                ))

            assistant_msg = result_container.get("message")
            if not assistant_msg:
                break

            self.messages.append(assistant_msg)
            tool_calls = assistant_msg.get("tool_calls")

            # If no tool calls, print final answer and return
            if not tool_calls:
                content = assistant_msg.get("content", "")
                if content:
                    final_response_text = content
                    self.console.print(f"\n[bold green]AI:[/bold green] {content}\n")
                break

            # Process tool calls
            reference_feedback = []
            for call_index, tc in enumerate(tool_calls):
                fn = tc["function"]
                fn_name = fn["name"]
                fn_args = fn["arguments"]

                # Repeat-Tool Guard: detect consecutive identical calls
                current_sig = f"{fn_name}:{fn_args.strip()}"
                if current_sig == self._last_tool_sig:
                    self._repeat_call_count += 1
                else:
                    self._last_tool_sig = current_sig
                    self._repeat_call_count = 1

                # Display tool invocation
                self._display_tool_call(fn_name, fn_args)

                # Execute tool
                tool_output = dispatch_tool_call(fn_name, fn_args)

                # Display tool result
                self._display_tool_result(fn_name, tool_output)

                # Append tool result to messages
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": fn_name,
                    "content": tool_output
                })
                if any(marker in tool_output for marker in ("API_PRECHECK:", "AttributeError:", "TypeError:")):
                    try:
                        script = json.loads(fn_args).get("script", "")
                    except (ValueError, AttributeError):
                        script = ""
                    # Finish every tool result before adding another system
                    # reference, preserving multi-call API message ordering.
                    reference_feedback.append(doc_searcher.runtime_context(
                        tool_output + "\n" + script, limit=3, max_chars=6000))
                    for skipped in tool_calls[call_index + 1:]:
                        self.messages.append({
                            "role": "tool", "tool_call_id": skipped["id"],
                            "name": skipped["function"]["name"],
                            "content": "NOT_EXECUTED: the earlier API task failed or was rejected. Replan from the corrected reference and inspect any partial runtime changes."
                        })
                    break

                if "OUTCOME_UNKNOWN:" in tool_output or "BRIDGE_BLOCKED:" in tool_output:
                    # Complete the tool-call protocol without executing any of the
                    # remaining calls. A lost result must never cause blind replay.
                    for skipped in tool_calls[call_index + 1:]:
                        self.messages.append({
                            "role": "tool", "tool_call_id": skipped["id"],
                            "name": skipped["function"]["name"],
                            "content": "NOT_EXECUTED: stopped because the bridge is blocked or an earlier task outcome is unknown."
                        })
                    if "BRIDGE_BLOCKED:" in tool_output:
                        message = "Abaqus GUI 桥接被阻塞，已停止本轮后续调用。请根据以下原因恢复 GUI 后再试。\n" + tool_output
                    else:
                        message = "任务执行结果尚未确认，已停止本轮后续操作，避免重复修改模型。\n" + tool_output
                    self.messages.append({"role": "assistant", "content": message})
                    self.console.print(message)
                    return message

                # Anti-looping reminder if stuck
                if self._repeat_call_count >= 2:
                    self.console.print("[dim yellow]  [Guard] Repeated tool call detected. Injecting anti-loop reminder.[/dim yellow]")
                    self.messages.append({
                        "role": "system",
                        "content": (
                            f"[System Loop-Breaker Notice] Repeated call to '{fn_name}' detected ({self._repeat_call_count} times). "
                            "This call did not make progress. Do NOT run the exact same parameters again. "
                            "Analyze the last output/error, change your approach, or finish the task."
                        )
                    })

            if reference_feedback:
                self.messages.append({"role": "system", "name": "abaqus_runtime_reference",
                    "content": "API repair reference. Inspect partial changes after runtime failures; "
                               "a precheck rejection means this task never executed.\n\n"
                               + reference_feedback[-1]})

        if step_count >= MAX_AGENT_STEPS:
            self.console.print("[bold yellow]Warning: Reached maximum step limit for single turn.[/bold yellow]")

        return final_response_text

    def _display_tool_call(self, fn_name: str, args_json: str) -> None:
        """Render tool call header and arguments in terminal."""
        try:
            parsed = json.loads(args_json)
        except Exception:
            parsed = args_json

        if fn_name == "abaqus_run_script" and isinstance(parsed, dict) and "script" in parsed:
            py_code = parsed["script"].strip()
            syntax = Syntax(py_code, "python", theme="monokai", line_numbers=True)
            self.console.print(Panel(syntax, title="[bold green]Executing Python in Abaqus[/bold green]", border_style="green"))
        else:
            args_str = json.dumps(parsed, ensure_ascii=False, indent=2) if isinstance(parsed, (dict, list)) else str(parsed)
            self.console.print(f"\n[bold blue]Tool Call: {fn_name}[/bold blue]\n[dim]{args_str}[/dim]")

    def _display_tool_result(self, fn_name: str, output: str) -> None:
        """Render tool execution output summary in terminal."""
        lines = output.strip().splitlines()
        preview = "\n".join(lines[:15])
        if len(lines) > 15:
            preview += f"\n... ({len(lines) - 15} more lines truncated)"

        if output.startswith("ERROR") or "failed" in output.lower():
            self.console.print(Panel(preview, title=f"[bold red]{fn_name} Result (Error)[/bold red]", border_style="red"))
        else:
            self.console.print(Panel(preview, title=f"[bold cyan]{fn_name} Result[/bold cyan]", border_style="cyan"))
