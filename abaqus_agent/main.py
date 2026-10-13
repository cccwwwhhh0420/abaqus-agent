# -*- coding: utf-8 -*-
"""Interactive Command-Line Interface for Abaqus AI Agent."""

import sys
import json
from pathlib import Path

# Ensure project directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markup import escape

from abaqus_agent.agent.core import AbaqusAgent
from abaqus_agent.agent.llm import LLMClient
from abaqus_agent.bridge.client import (
    diff_snapshots,
    describe_bridge_status,
    find_abaqus_executable,
    get_status,
    get_task_result,
    launch_abaqus_cae,
    list_snapshots,
    load_snapshot,
    ping,
    run_py,
    take_snapshot,
)
from abaqus_agent.config import (
    ABAQUS_HOST,
    ABAQUS_PORT,
    API_KEY,
    AUTO_LAUNCH_ABAQUS,
    BASE_URL,
    MODEL_NAME,
    WORKSPACE_DIR,
)
from abaqus_agent.tools.doc_search import doc_searcher


def print_banner(console: Console) -> None:
    """Display startup banner with environment status."""
    from abaqus_agent.bridge.client import get_ipc_status
    ipc_info = get_ipc_status()
    color = "yellow" if ipc_info.get("status") in ("blocked", "executing") else "green"
    if not ipc_info.get("alive"):
        color = "red"
    bridge_status = f"[bold {color}]{escape(describe_bridge_status(ipc_info))}[/bold {color}]"

    doc_searcher.load()
    cmd_count = len(doc_searcher.commands)

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column()
    table.add_row("LLM Model:", f"[yellow]{MODEL_NAME}[/yellow] ({BASE_URL})")
    table.add_row("API Key:", "[green]Configured[/green]" if API_KEY else "[red]Missing (Set OPENAI_API_KEY in .env)[/red]")
    table.add_row("Abaqus Bridge:", bridge_status)
    table.add_row("API Knowledgebase:", f"[green]Abaqus 2025: {len(doc_searcher.entries)} reference cards / {cmd_count} API entries[/green]")

    panel = Panel(
        table,
        title="[bold white]Abaqus CAE AI Command Center[/bold white]",
        subtitle="[dim]Type prompt or /help for slash commands | Ctrl+C to exit[/dim]",
        border_style="blue"
    )
    console.print(panel)


def print_help(console: Console) -> None:
    """Print available slash commands."""
    table = Table(title="Available Slash Commands", border_style="cyan")
    table.add_column("Command", style="bold green")
    table.add_column("Description")
    table.add_row("/launch [dir]", "Launch Abaqus CAE with bridge mounted in specified directory")
    table.add_row("/workspace, /pwd", "Show active Abaqus directory, abaqus.rpy and .cae/.inp/.odb files")
    table.add_row("/log [n]", "Read last n lines of user GUI actions from abaqus.rpy (default: 30)")
    table.add_row("/status", "Get current entity counts (parts, materials, sections, steps, loads, bcs, mesh)")
    table.add_row("/run <py>", "Directly execute Python script in Abaqus without LLM")
    table.add_row("/search <query>", "Search Abaqus Python API documentation and examples")
    table.add_row("/snapshot [tag]", "Capture and save a full model state snapshot")
    table.add_row("/diff [a] [b]", "Compare two snapshots to detect user modifications")
    table.add_row("/ping", "Check connection status with Abaqus bridge")
    table.add_row("/task <id>", "Inspect a durable task result without another kernel command")
    table.add_row("/reset", "Clear conversation history and start a new session")
    table.add_row("/help", "Show this help table")
    table.add_row("/exit, /quit", "Exit the CLI application")
    console.print(table)


def handle_slash_command(cmd_line: str, agent: AbaqusAgent, console: Console) -> bool:
    """Process slash command. Returns True if handled."""
    parts = cmd_line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ["/exit", "/quit"]:
        console.print("[bold yellow]Exiting Abaqus AI Command Center. Goodbye![/bold yellow]")
        sys.exit(0)

    elif cmd == "/help":
        print_help(console)
        return True

    elif cmd == "/ping":
        if ping():
            console.print(describe_bridge_status(), markup=False)
        else:
            console.print("[bold red]Abaqus bridge is OFFLINE. Type /launch to start Abaqus CAE.[/bold red]")
        return True

    elif cmd == "/task":
        console.print(json.dumps(get_task_result(arg.strip()), ensure_ascii=False, indent=2))
        return True

    elif cmd == "/launch":
        target_dir = arg.strip() if arg.strip() else None
        with console.status("[dim cyan]Launching Abaqus CAE with AI Bridge...[/dim cyan]", spinner="dots"):
            ok, msg = launch_abaqus_cae(workspace_dir=target_dir)
        if ok:
            console.print(f"[bold green]{msg}[/bold green]")
        else:
            console.print(f"[bold red]Launch failed: {msg}[/bold red]")
        return True

    elif cmd in ["/workspace", "/pwd"]:
        from abaqus_agent.tools.abaqus_tools import tool_abaqus_get_workspace
        res = tool_abaqus_get_workspace()
        console.print(Panel(res, title="Abaqus Active Workspace", border_style="cyan"))
        return True

    elif cmd == "/log":
        from abaqus_agent.tools.abaqus_tools import tool_abaqus_read_gui_log
        lines = int(arg) if arg.isdigit() else 30
        res = tool_abaqus_read_gui_log(tail_lines=lines)
        console.print(Panel(res, title="Abaqus GUI Replay Log (abaqus.rpy)", border_style="cyan"))
        return True

    elif cmd == "/status":
        from abaqus_agent.tools.abaqus_tools import tool_abaqus_status
        res = tool_abaqus_status()
        console.print(Panel(res, title="Abaqus Model Status", border_style="green"))
        return True

    elif cmd == "/run":
        if not arg:
            console.print("[yellow]Usage: /run <python code>[/yellow]")
            return True
        ok, res = run_py(arg)
        if ok:
            console.print(Panel(res if res else "(Success - No output)", title="Python Execution Output", border_style="green"))
        else:
            console.print(Panel(res, title="Python Execution Error", border_style="red"))
        return True

    elif cmd == "/search":
        if not arg:
            console.print("[yellow]Usage: /search <keyword>[/yellow]")
            return True
        res = doc_searcher.search(arg, limit=5, full_detail=True)
        console.print(Panel(res, title=f"API Documentation Search: '{arg}'", border_style="cyan"))
        return True

    elif cmd == "/snapshot":
        ok, path, data = take_snapshot(tag=arg if arg else None)
        if ok:
            counts = data.get("counts", {})
            counts_str = ", ".join([f"{k.upper()}={v}" for k, v in counts.items()])
            console.print(f"[bold green]Snapshot saved:[/bold green] {path}\n[dim]Summary: {counts_str}[/dim]")
        else:
            console.print(f"[bold red]Snapshot failed: {path}[/bold red]")
        return True

    elif cmd == "/diff":
        snaps = list_snapshots()
        if not snaps:
            console.print("[yellow]No snapshots available. Use /snapshot to create one first.[/yellow]")
            return True
        tokens = arg.split()
        if len(tokens) >= 2:
            snap_a, snap_b = tokens[0], tokens[1]
        elif len(snaps) >= 2:
            snap_a, snap_b = str(snaps[-2]), str(snaps[-1])
        else:
            console.print(f"[yellow]Need at least 2 snapshots. Only found 1: {snaps[0].name}[/yellow]")
            return True

        try:
            da = load_snapshot(snap_a)
            db = load_snapshot(snap_b)
            diff_text = diff_snapshots(da, db)
            console.print(Panel(diff_text, title=f"Diff: {Path(snap_a).name} vs {Path(snap_b).name}", border_style="cyan"))
        except Exception as e:
            console.print(f"[bold red]Diff failed: {e}[/bold red]")
        return True

    elif cmd in ["/reset", "/clear"]:
        agent.reset()
        console.print("[bold green]Conversation context reset.[/bold green]")
        return True

    return False


def main() -> None:
    """Run interactive REPL loop."""
    console = Console()

    # Check and auto-launch Abaqus CAE if configured
    if not ping() and AUTO_LAUNCH_ABAQUS:
        console.print(f"[dim cyan]Abaqus bridge is offline. Auto-launching Abaqus CAE in '{WORKSPACE_DIR}'...[/dim cyan]")
        with console.status("[dim cyan]Launching Abaqus CAE...[/dim cyan]", spinner="dots"):
            ok, msg = launch_abaqus_cae(workspace_dir=str(WORKSPACE_DIR))
        if ok:
            console.print(f"[bold green]{msg}[/bold green]")
        else:
            console.print(f"[bold yellow]{msg}[/bold yellow]")

    print_banner(console)

    llm = LLMClient()
    agent = AbaqusAgent(llm_client=llm, console=console)

    if not llm.is_configured():
        console.print(
            "\n[bold yellow]Notice:[/bold yellow] No API Key detected. "
            "You can still use slash commands (e.g. [bold green]/run[/bold green], [bold green]/search[/bold green], [bold green]/status[/bold green]), "
            "or configure [bold cyan].env[/bold cyan] to enable AI chat.\n"
        )

    while True:
        try:
            user_input = console.input("\n[bold white]Abaqus-AI >[/bold white] ").strip()
            if not user_input:
                continue

            if user_input.startswith("/"):
                if handle_slash_command(user_input, agent, console):
                    continue

            # Run agent conversation turn
            try:
                agent.run_turn(user_input)
            except Exception as e:
                console.print(f"[bold red]Agent error: {e}[/bold red]")

        except (KeyboardInterrupt, EOFError):
            console.print("\n[bold yellow]Exiting...[/bold yellow]")
            break


if __name__ == "__main__":
    main()
