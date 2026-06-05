"""
Console Utilities
==================
Colored output, progress bars, and formatting helpers.
"""

import sys
import os

# Force UTF-8 output on Windows
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from datetime import datetime


# ============================================================
# ANSI COLORS
# ============================================================

class C:
    """ANSI color codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"

    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"


def banner(text: str, char: str = "═", width: int = 64) -> None:
    """Print a fancy banner box."""
    border = char * width
    padding = (width - len(text) - 2) // 2
    print(f"\n{C.CYAN}{C.BOLD}╔{border}╗")
    print(f"║{' ' * padding}{text}{' ' * (width - padding - len(text))}║")
    print(f"╚{border}╝{C.RESET}\n")


def section(title: str) -> None:
    """Print a section header."""
    print(f"\n{C.BOLD}{C.BLUE}══ {title} ══{C.RESET}")


def success(msg: str) -> None:
    print(f"  {C.GREEN}✓{C.RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {C.YELLOW}⚠{C.RESET} {msg}")


def error(msg: str) -> None:
    print(f"  {C.RED}✗{C.RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {C.CYAN}ℹ{C.RESET} {msg}")


def dim(msg: str) -> None:
    print(f"  {C.DIM}{msg}{C.RESET}")


def call_log(
    index: int,
    total: int,
    tenant: str,
    agent: str,
    decision: str,
    tokens: int,
    cost: float,
    latency: int,
) -> None:
    """Print a formatted call log line."""
    # Decision color
    if decision == "allow":
        dec_color = C.GREEN
        dec_icon = "✓"
    elif "downgrade" in decision:
        dec_color = C.YELLOW
        dec_icon = "↓"
    elif decision == "block":
        dec_color = C.RED
        dec_icon = "✗"
    else:
        dec_color = C.GRAY
        dec_icon = "?"

    ts = datetime.now().strftime("%H:%M:%S")
    print(
        f"  {C.DIM}[{index}/{total}]{C.RESET} "
        f"{C.GRAY}{ts}{C.RESET} "
        f"{dec_color}{dec_icon} {decision.upper():15s}{C.RESET} "
        f"{C.BOLD}{tenant:20s}{C.RESET} "
        f"{agent:20s} "
        f"tok={tokens:>6,} "
        f"${cost:>8.5f} "
        f"{latency:>5}ms"
    )


def anomaly_alert(tenant: str, anomaly_type: str, severity: str, description: str) -> None:
    """Print a formatted anomaly alert."""
    sev_colors = {
        "critical": C.BG_RED + C.WHITE,
        "high": C.RED,
        "warning": C.YELLOW,
        "normal": C.GREEN,
    }
    color = sev_colors.get(severity, C.GRAY)
    print(f"\n  {color}{C.BOLD} 🚨 ANOMALY DETECTED {C.RESET}")
    print(f"    {C.BOLD}Tenant:{C.RESET}   {tenant}")
    print(f"    {C.BOLD}Type:{C.RESET}     {anomaly_type}")
    print(f"    {C.BOLD}Severity:{C.RESET} {color}{severity.upper()}{C.RESET}")
    print(f"    {C.BOLD}Detail:{C.RESET}   {description}")
    print()


def governance_alert(tenant: str, decision: str, model: str, reason: str) -> None:
    """Print a formatted governance decision alert."""
    if decision == "block":
        color = C.RED
        icon = "🛑"
    else:
        color = C.YELLOW
        icon = "⚡"
    print(f"\n  {color}{C.BOLD} {icon} GOVERNANCE: {decision.upper()} {C.RESET}")
    print(f"    {C.BOLD}Tenant:{C.RESET} {tenant}")
    print(f"    {C.BOLD}Model:{C.RESET}  {model}")
    print(f"    {C.BOLD}Reason:{C.RESET} {reason}")
    print()


def progress_bar(current: int, total: int, label: str = "", width: int = 40) -> None:
    """Print an inline progress bar."""
    pct = current / total if total > 0 else 0
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    sys.stdout.write(f"\r  {C.CYAN}{bar}{C.RESET} {pct*100:5.1f}% {label}")
    if current >= total:
        sys.stdout.write("\n")
    sys.stdout.flush()


def demo_act(act_num: int, title: str, description: str) -> None:
    """Print a demo act header."""
    print(f"\n{'─' * 64}")
    print(f"{C.BOLD}{C.MAGENTA}  ACT {act_num}: {title}{C.RESET}")
    print(f"  {C.DIM}{description}{C.RESET}")
    print(f"{'─' * 64}")


def wait_for_enter(message: str = "Press ENTER to continue...") -> None:
    """Pause and wait for user input."""
    input(f"\n  {C.YELLOW}▶ {message}{C.RESET}")
