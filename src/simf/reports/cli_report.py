from rich.console import Console
from rich.table import Table

from ..core.character import Character
from ..core.metrics import SimResult
from ..io.combat_log import LogSummary
from ..optimizer.stat_weights import StatWeight


def _stat_block(character: Character) -> Table:
    table = Table(title="Character stats", show_header=False, box=None, padding=(0, 2))
    table.add_column("Stat", style="dim")
    table.add_column("Value", style="cyan", justify="right")
    table.add_row("Max HP", f"{character.max_hp():,.0f}")
    table.add_row("Armor", f"{character.total_armor():,.0f}  (DR {character.armor_dr():.1%})")
    table.add_row(
        "Versatility", f"{character.versatility_pct():.2%}  (DR {character.versatility_dr():.2%})"
    )
    table.add_row("eHP physical", f"{character.effective_hp_physical():,.0f}")
    table.add_row("eHP magic", f"{character.effective_hp_magic():,.0f}")
    return table


def render_summary(
    result: SimResult, character: Character, profile_name: str, healer_name: str
) -> None:
    console = Console()
    console.print()
    console.print("[bold cyan]simf — Survivability Report[/bold cyan]")
    console.print(
        f"Character: [bold]{character.name}[/bold] "
        f"({character.race} {character.class_spec}, talents: {character.talents})"
    )
    console.print(
        f"Damage profile: [bold]{profile_name}[/bold] | "
        f"Healer: [bold]{healer_name}[/bold] | "
        f"Iterations: {result.iterations}"
    )
    console.print()
    console.print(_stat_block(character))
    console.print()

    table = Table(title="Survivability metrics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green", justify="right")

    table.add_row("Death rate", f"{result.death_rate:.2%}")
    table.add_row("Deaths / iterations", f"{result.deaths} / {result.iterations}")
    table.add_row("M+TMI (12s window)", f"{result.m_plus_tmi:,.0f}")
    table.add_row("Mean DTPS", f"{result.mean_dtps:,.0f}")
    table.add_row("p99 DTPS", f"{result.p99_dtps:,.0f}")
    table.add_row("Mean 5s window damage", f"{result.mean_5s_window:,.0f}")
    table.add_row("p95 5s window", f"{result.p95_5s_window:,.0f}")
    table.add_row("p99 5s window", f"{result.p99_5s_window:,.0f}")
    table.add_row("p99 10s window", f"{result.p99_10s_window:,.0f}")
    table.add_row("p99 15s window", f"{result.p99_15s_window:,.0f}")

    console.print(table)


def render_ab(
    result_a: SimResult,
    label_a: str,
    result_b: SimResult,
    label_b: str,
    char_a: "Character | None" = None,
    char_b: "Character | None" = None,
) -> None:
    console = Console()

    if char_a is not None and char_b is not None:
        stat_table = Table(title=f"Static stats: {label_a} vs {label_b}")
        stat_table.add_column("Stat", style="cyan")
        stat_table.add_column(label_a, justify="right", style="green")
        stat_table.add_column(label_b, justify="right", style="yellow")
        stat_table.add_column(f"Δ ({label_b}−{label_a})", justify="right", style="magenta")

        def _delta_row(name: str, a_val: float, b_val: float, fmt: str = "{:,.0f}") -> None:
            delta = b_val - a_val
            sign = "+" if delta > 0 else ""
            stat_table.add_row(
                name, fmt.format(a_val), fmt.format(b_val), f"{sign}{fmt.format(delta)}"
            )

        _delta_row("Max HP", char_a.max_hp(), char_b.max_hp())
        _delta_row("Armor", char_a.total_armor(), char_b.total_armor())
        _delta_row("Armor DR", char_a.armor_dr(), char_b.armor_dr(), "{:.1%}")
        _delta_row("Versatility", char_a.versatility_pct(), char_b.versatility_pct(), "{:.2%}")
        _delta_row("eHP physical", char_a.effective_hp_physical(), char_b.effective_hp_physical())
        _delta_row("eHP magic", char_a.effective_hp_magic(), char_b.effective_hp_magic())
        console.print()
        console.print(stat_table)

    table = Table(title=f"A/B comparison: {label_a} vs {label_b}")
    table.add_column("Metric", style="cyan")
    table.add_column(label_a, style="green", justify="right")
    table.add_column(label_b, style="yellow", justify="right")
    table.add_column(f"Δ ({label_b} − {label_a})", style="magenta", justify="right")

    metrics = [
        ("Death rate", lambda r: r.death_rate, "{:.2%}"),
        ("M+TMI", lambda r: r.m_plus_tmi, "{:,.0f}"),
        ("Mean DTPS", lambda r: r.mean_dtps, "{:,.0f}"),
        ("p99 5s window", lambda r: r.p99_5s_window, "{:,.0f}"),
        ("p99 10s window", lambda r: r.p99_10s_window, "{:,.0f}"),
        ("p99 15s window", lambda r: r.p99_15s_window, "{:,.0f}"),
    ]

    for name, getter, fmt in metrics:
        a = getter(result_a)
        b = getter(result_b)
        delta = b - a
        pct = (delta / a * 100) if a not in (0, 0.0) else 0.0
        table.add_row(name, fmt.format(a), fmt.format(b), f"{fmt.format(delta)} ({pct:+.2f}%)")

    console.print()
    console.print(table)


def render_stat_weights(
    weights: dict[str, StatWeight],
    character: Character,
    metric: str,
    iterations: int,
    delta_rating: int,
) -> None:
    from ..core.constants import load_constants

    console = Console()
    console.print()
    console.print("[bold cyan]simf — Stat Weights[/bold cyan]")
    console.print(f"Character: [bold]{character.name}[/bold] | Metric: [bold]{metric}[/bold]")
    console.print(f"Iterations per perturbation: {iterations} | Δ rating: ±{delta_rating}")
    console.print(
        "[dim]Convention: lower metric = better survivability. "
        "A negative weight means increasing this stat REDUCES the metric (good).[/dim]"
    )
    console.print()
    console.print(_stat_block(character))
    console.print()

    dps_weights = load_constants().get("dps_stat_weights", {})

    table = Table(title=f"Δ {metric} per +1000 rating (sorted best → worst for survivability)")
    table.add_column("Stat", style="cyan")
    table.add_column(f"{metric} @ −Δ", justify="right", style="dim")
    table.add_column(f"{metric} @ base", justify="right", style="dim")
    table.add_column(f"{metric} @ +Δ", justify="right", style="dim")
    table.add_column("Surv weight/1k", justify="right", style="bold")
    table.add_column("DPS weight/1k", justify="right", style="bold yellow")

    sorted_weights = sorted(weights.values(), key=lambda w: w.weight_per_1000_rating)
    for w in sorted_weights:
        stat_name = w.stat.replace("_rating", "").title()
        weight_color = (
            "green"
            if w.weight_per_1000_rating < 0
            else "red"
            if w.weight_per_1000_rating > 0
            else "white"
        )
        surv_str = f"[{weight_color}]{w.weight_per_1000_rating:+,.1f}[/{weight_color}]"
        dps_val = dps_weights.get(w.stat)
        dps_str = f"{dps_val:.2f}" if dps_val is not None else "—"
        table.add_row(
            stat_name,
            f"{w.low_value:,.0f}",
            f"{w.base_value:,.0f}",
            f"{w.high_value:,.0f}",
            surv_str,
            dps_str,
        )
    console.print(table)


def render_log_summary(summary: LogSummary, character: str, top_n: int = 10) -> None:
    console = Console()
    run = summary.run
    duration_s = summary.duration_s
    duration_str = f"{int(duration_s // 60)}:{int(duration_s % 60):02d}"
    dtps_post = summary.total_amount / duration_s if duration_s > 0 else 0
    dtps_base = summary.total_base_amount / duration_s if duration_s > 0 else 0

    console.print()
    console.print("[bold cyan]simf — Combat Log Analysis[/bold cyan]")
    console.print(
        f"Character: [bold]{character}[/bold] | "
        f"{run.map_name} +{run.key_level} | "
        f"Affixes: {run.affixes} | "
        f"Duration: {duration_str} | "
        f"Result: {'TIMED' if run.success else 'DEPLETED' if run.success is False else '?'}"
    )
    console.print()

    table = Table(title="Damage taken — totals")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="green")
    table.add_row("Damage events", f"{summary.event_count:,}")
    table.add_row("Damage to HP (post-mit)", f"{summary.total_amount:,}")
    table.add_row("Damage attempted (pre-mit)", f"{summary.total_base_amount:,}")
    table.add_row("Blocked", f"{summary.total_blocked:,}")
    table.add_row("Absorbed", f"{summary.total_absorbed:,}")
    table.add_row("Resisted", f"{summary.total_resisted:,}")
    table.add_row("DTPS (post-mit)", f"{dtps_post:,.0f}")
    table.add_row("DTPS (pre-mit baseAmount)", f"{dtps_base:,.0f}")
    console.print(table)

    if summary.total_amount > 0:
        school_table = Table(title="Damage taken — by school")
        school_table.add_column("School", style="cyan")
        school_table.add_column("Damage", justify="right", style="green")
        school_table.add_column("% of total", justify="right", style="dim")
        for school, amt in sorted(summary.by_school.items(), key=lambda kv: -kv[1]):
            pct = amt / summary.total_amount * 100
            school_table.add_row(school, f"{amt:,}", f"{pct:.1f}%")
        console.print(school_table)

    if summary.by_source_amount:
        src_table = Table(title=f"Top {top_n} damage sources")
        src_table.add_column("Source", style="cyan")
        src_table.add_column("Hits", justify="right", style="dim")
        src_table.add_column("Total damage", justify="right", style="green")
        src_table.add_column("Mean per hit", justify="right", style="dim")
        sources = sorted(summary.by_source_amount.items(), key=lambda kv: -kv[1])[:top_n]
        for src, amt in sources:
            count = summary.by_source_count.get(src, 1)
            mean = amt / count if count > 0 else 0
            src_table.add_row(src, f"{count:,}", f"{amt:,}", f"{mean:,.0f}")
        console.print(src_table)

    if summary.by_ability_amount:
        ab_table = Table(title=f"Top {top_n} damage abilities")
        ab_table.add_column("Ability", style="cyan")
        ab_table.add_column("Hits", justify="right", style="dim")
        ab_table.add_column("Total damage", justify="right", style="green")
        ab_table.add_column("Mean per hit", justify="right", style="dim")
        abilities = sorted(summary.by_ability_amount.items(), key=lambda kv: -kv[1])[:top_n]
        for ability, amt in abilities:
            count = summary.by_ability_count.get(ability, 1)
            mean = amt / count if count > 0 else 0
            ab_table.add_row(ability, f"{count:,}", f"{amt:,}", f"{mean:,.0f}")
        console.print(ab_table)
