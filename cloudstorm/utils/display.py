"""
Display utilities for cloudstorm.
Clean console output using rich.
"""

from rich import box
from rich.console import Console
from rich.table import Table

console = Console()
_quiet = False


def set_quiet(quiet: bool):
    """Set quiet mode (suppress banner)."""
    global _quiet
    _quiet = quiet


def print_banner():
    """Print the cloudstorm banner."""
    if _quiet:
        return
    banner = """
     _____ _                 _     _
    / ____| |               | |   | |
   | |    | | ___  _   _  __| |___| |_ ___  _ __ _ __ ___
   | |    | |/ _ \\| | | |/ _` / __| __/ _ \\| '__| '_ ` _ \\
   | |____| | (_) | |_| | (_| \\__ \\ || (_) | |  | | | | | |
    \\_____|_|\\___/ \\__,_|\\__,_|___/\\__\\___/|_|  |_| |_| |_|

    Distributed load testing across AWS regions
    """
    console.print(banner, style="bold cyan")


def print_info(message: str):
    """Print an info message."""
    console.print(f"[blue]INFO[/blue] {message}")


def print_success(message: str):
    """Print a success message."""
    console.print(f"[green]OK[/green] {message}")


def print_warning(message: str):
    """Print a warning message."""
    console.print(f"[yellow]WARN[/yellow] {message}")


def print_error(message: str):
    """Print an error message."""
    console.print(f"[red]ERROR[/red] {message}")


def print_table(headers: list[str], rows: list[list[str]], title: str | None = None):
    """Print a formatted table."""
    table = Table(title=title, box=box.ROUNDED)

    for header in headers:
        table.add_column(header, style="cyan")

    for row in rows:
        table.add_row(*[str(cell) for cell in row])

    console.print(table)


def print_key_value(key: str, value: str):
    """Print a key-value pair."""
    console.print(f"[cyan]{key}:[/cyan] {value}")


def confirm(message: str, default: bool = False) -> bool:
    """Ask for user confirmation."""
    suffix = "[Y/n]" if default else "[y/N]"
    response = console.input(f"{message} {suffix}: ").strip().lower()

    if not response:
        return default

    return response in ("y", "yes")


def render_live_dashboard(elapsed: int, duration: int, regions: dict, total_instances: int) -> Table:
    """
    Render a live dashboard table for the load test.

    Args:
        elapsed: Seconds elapsed.
        duration: Total test duration in seconds.
        regions: Dict of region -> {"requests": N, "errors": N, "instances": N}.
        total_instances: Total instance count.

    Returns:
        rich.table.Table for use with rich.live.
    """
    total_reqs = sum(r.get("requests", 0) for r in regions.values())
    total_errs = sum(r.get("errors", 0) for r in regions.values())
    err_rate = (total_errs / total_reqs * 100) if total_reqs > 0 else 0
    rps = total_reqs / elapsed if elapsed > 0 else 0

    table = Table(box=box.SIMPLE_HEAVY, show_header=False, padding=(0, 2))
    table.add_column("key", style="cyan", min_width=12)
    table.add_column("value", min_width=50)

    bar_len = 30
    progress = min(elapsed / duration, 1.0) if duration > 0 else 0
    filled = int(bar_len * progress)
    bar = "[green]" + "=" * filled + "[/green]" + "[dim]" + "-" * (bar_len - filled) + "[/dim]"
    time_str = f"{elapsed}s / {duration}s"

    table.add_row("cloudstorm", f"{bar}  {time_str}  |  {total_instances} instances")
    table.add_row("", "")
    table.add_row("Requests", f"[bold]{total_reqs:,}[/bold]")
    table.add_row("Req/s", f"[bold]{rps:,.0f}[/bold]")
    table.add_row("Errors",
                  f"[bold red]{total_errs:,}[/bold red] ({err_rate:.1f}%)" if total_errs > 0
                  else "[bold green]0[/bold green] (0.0%)")
    table.add_row("", "")

    for region, data in regions.items():
        r_reqs = data.get("requests", 0)
        r_errs = data.get("errors", 0)
        r_rate = (r_errs / r_reqs * 100) if r_reqs > 0 else 0
        r_rps = r_reqs / elapsed if elapsed > 0 else 0
        table.add_row(
            f"  {region}",
            f"{r_reqs:,} reqs  |  {r_rps:,.0f}/s  |  {r_rate:.1f}% err"
        )

    return table
