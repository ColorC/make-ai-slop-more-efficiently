"""Locate and read the inputs the run-metrics dashboard is derived from.

Nothing here interprets a run; it only finds the feasibility reports, the
replay index entries and the cross-reference products, and hands them over as
plain dictionaries.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any, Iterator

GAME_ID = "romantic-restaurant"
RUN_INDEX_SCHEMA = "game-observatory.replay.run-index-entry.v1"
DRIVE_LOG_SUFFIX = ".drive.jsonl"
DRIVE_SCHEMA = "omnicompany.game-observatory.rr-unattended-drive.v1"
COMPARISON_SCHEMA = "omnicompany.game-observatory.rr-drive-comparison.v1"
PROJECTION_SCHEMA = "omnicompany.game-observatory.rr-progress-projection.v1"
MULTI_DAY_SCHEMA = "omnicompany.game-observatory.rr-f4-08.v1"
LIVE_PROBE_SCHEMA = "omnicompany.game-observatory.rr-save-read-live-probe.v1"
LATENCY_SCHEMA = "omnicompany.game-observatory.rr-save-read-latency.v1"

# ``F4-07``, ``F4-01b`` -- the spec number a product file is named after.
F_NUMBER = re.compile(r"^(F\d+-\d+[A-Za-z]*)")


def f_number(name: str) -> str | None:
    match = F_NUMBER.match(name)
    return match.group(1) if match else None


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "data").is_dir():
            return parent
    raise RuntimeError("repository root not found")


def default_paths(root: Path | None = None) -> dict[str, Path]:
    base = root or repo_root()
    model = base / "data/domains/game_observatory/game_models" / GAME_ID
    return {
        "root": base,
        "feasibility": model / "feasibility",
        "facts": model / "crossref/facts.v1.ndjson",
        "summary": model / "crossref/summary.v1.json",
        "replay": base / "data/domains/game_observatory/replay",
        "out_dir": model / "metrics",
        # F6-05 section 3.2.  The guardian writes one markdown report per
        # calendar day here; the dashboard reads which days it covers.
        "daily": base
        / "docs/plans/game-observatory"
        / "[2026-09-05]ROMANTIC-RESTAURANT-FULL-DELEGATION-v1"
        / "records/daily",
        # EnergyModel's recovery clock, as the design extraction wrote it from
        # the decompiled source.  The theoretical ceiling on energy-bound
        # progress is read from here rather than restated in this package.
        "energy_constants": base
        / "docs/projects/game-observatory/cases/romantic-restaurant/design/energy-items.json",
        # The surveyed navigation whitelist.  The free-claim audit judges a tap
        # against the registration that authorised it, so the registry is read
        # from the same file the driver reads rather than restated here.
        "navigation": base
        / "config/game_observatory/romantic_restaurant/navigation.v1.json",
    }


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_atomic(path: Path, text: str) -> None:
    """Write through a sibling temporary file and rename it into place.

    Opening a product directly for truncation intermittently fails on this host
    while another reader still holds the file it has just read.  Writing a new
    file and renaming it also means a reader never sees a half-written product.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_if_changed(path: Path, text: str) -> bool:
    """Rewrite the product only when its bytes would change."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    write_atomic(path, text)
    return True


@dataclass(frozen=True)
class Report:
    """One feasibility report file, kept next to the name it was read from."""

    path: Path
    payload: dict[str, Any]

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def file_task(self) -> str:
        """The F-number the report file is named after, e.g. ``F4-04``.

        rr_play names its report after the *policy* it ran, so every segment of
        an F4-07 drive lands in a file called ``F4-04-<ts>.json``.  The file
        name is therefore the weakest of the task labels and only used once the
        report's own fields and the owning drive have both stayed silent.
        """

        return f_number(self.path.stem) or self.path.stem.split("-")[0]

    @property
    def declared_task(self) -> tuple[str | None, str | None]:
        """The task the report names itself, and which field said so."""

        task = self.payload.get("task")
        if isinstance(task, str) and task.strip():
            return task.strip(), "report.task"
        spec = self.payload.get("spec")
        if isinstance(spec, str) and spec.strip():
            number = f_number(Path(spec.replace("\\", "/")).name)
            if number:
                return number, "report.spec"
        return None, None

    @property
    def run_id(self) -> str | None:
        replay = self.payload.get("replay")
        if isinstance(replay, dict) and isinstance(replay.get("run_id"), str):
            return replay["run_id"]
        run_id = self.payload.get("run_id")
        return run_id if isinstance(run_id, str) else None


def iter_reports(feasibility_dir: Path) -> Iterator[Report]:
    """Read every report that sits directly in the feasibility directory.

    Sub-directories hold replay stores and working files, not reports, so the
    scan stays flat on purpose.
    """

    for path in sorted(feasibility_dir.glob("*.json")):
        try:
            payload = read_json(path)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(payload, dict):
            yield Report(path=path, payload=payload)


def replay_roots(paths: dict[str, Path]) -> list[Path]:
    """The canonical replay store plus the per-task stores under feasibility."""

    roots = []
    canonical = paths["replay"]
    if (canonical / "index" / GAME_ID).is_dir():
        roots.append(canonical)
    feasibility = paths["feasibility"]
    if feasibility.is_dir():
        for candidate in sorted(feasibility.glob("*-replay")):
            if (candidate / "index" / GAME_ID).is_dir():
                roots.append(candidate)
    return roots


@dataclass(frozen=True)
class IndexEntry:
    """A replay run-index entry and the store root it was found in."""

    root: Path
    payload: dict[str, Any]

    @property
    def run_id(self) -> str:
        return str(self.payload.get("run_id", ""))

    @property
    def bytes_total(self) -> int | None:
        value = self.payload.get("bytes_total")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def run_dir(self) -> Path:
        return self.root / GAME_ID / self.run_id


def load_index_entries(roots: list[Path]) -> dict[str, IndexEntry]:
    entries: dict[str, IndexEntry] = {}
    for root in roots:
        for path in sorted((root / "index" / GAME_ID).glob("*.json")):
            try:
                payload = read_json(path)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(payload, dict) or not payload.get("run_id"):
                continue
            entry = IndexEntry(root=root, payload=payload)
            entries[entry.run_id] = entry
    return entries


def count_facts_by_report(facts_path: Path) -> tuple[Counter[str], str]:
    """Facts contributed per report, and how far the fact stream has indexed.

    The horizon is the newest capture time in the stream. A report captured
    after it has simply not been indexed yet, which is a different statement
    from "this run produced no facts".
    """

    counts: Counter[str] = Counter()
    horizon = ""
    if not facts_path.is_file():
        return counts, horizon
    for line in facts_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            fact = json.loads(line)
        except json.JSONDecodeError:
            continue
        source = fact.get("source_report")
        if isinstance(source, str):
            counts[Path(source).name] += 1
        captured = fact.get("captured_at")
        if isinstance(captured, str) and captured > horizon:
            horizon = captured
    return counts, horizon


# F7-04 sections 2.1 and 2.2.  The dashboard stops re-deriving what the ledger
# already extracted: the claim audit, the guard histogram and the read-back
# trend all read these fact families instead of re-walking the reports.
DASHBOARD_FACT_KINDS = (
    "free_claim_delta",
    "guard_event",
    "order_reserve_block",
    "save_read_timing",
    "story_advance",
)


def load_crossref_facts(
    facts_path: Path, kinds: tuple[str, ...] = DASHBOARD_FACT_KINDS
) -> dict[str, list[dict[str, Any]]]:
    """The named fact families, grouped by kind, in the order they were indexed.

    A torn line is skipped rather than failing the build: the gameplay line
    writes into the same tree while this runs.
    """

    grouped: dict[str, list[dict[str, Any]]] = {kind: [] for kind in kinds}
    if not facts_path.is_file():
        return grouped
    wanted = set(kinds)
    with facts_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                fact = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = fact.get("kind")
            if isinstance(kind, str) and kind in wanted:
                grouped[kind].append(fact)
    return grouped


@dataclass
class Citation:
    """An aggregate report naming one run as an attempt or a segment."""

    report: str
    role: str
    label: str


@dataclass(frozen=True)
class DriveLog:
    """One unattended-driver event stream, read as far as it is written.

    ``rr_drive`` appends a line per event while it plays, so a log read during
    a live drive normally ends in a half-written line.  That line is counted in
    ``malformed_lines`` and skipped; nothing here rewrites the driver's file.
    """

    path: Path
    events: list[dict[str, Any]]
    malformed_lines: int

    @property
    def drive_id(self) -> str:
        return self.path.name[: -len(DRIVE_LOG_SUFFIX)]

    @property
    def task(self) -> str | None:
        return f_number(self.drive_id)

    @property
    def report_path(self) -> Path:
        """The report the driver writes when it finishes, if it has yet."""

        return self.path.with_name(f"{self.drive_id}.json")


def read_drive_log(path: Path) -> DriveLog:
    events: list[dict[str, Any]] = []
    malformed = 0
    # Decoded leniently: a live drive can be mid-write inside a multi-byte
    # character, and that is a truncated tail line, not a damaged log.
    text = path.read_bytes().decode("utf-8", errors="replace")
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(payload, dict):
            events.append(payload)
        else:
            malformed += 1
    return DriveLog(path=path, events=events, malformed_lines=malformed)


def load_drive_logs(feasibility_dir: Path) -> list[DriveLog]:
    if not feasibility_dir.is_dir():
        return []
    return [
        read_drive_log(path)
        for path in sorted(feasibility_dir.glob(f"*{DRIVE_LOG_SUFFIX}"))
    ]


@dataclass(frozen=True)
class DriveAttribution:
    """Which drive a run was a segment of, and where in the sequence."""

    drive_id: str
    task: str | None
    segment_index: int
    segment_label: str | None


def _run_id_from_dir(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return PurePath(value.replace("\\", "/")).name or None


def drive_attribution(logs: list[DriveLog]) -> dict[str, DriveAttribution]:
    """Map every run a drive played to that drive, by ``run_dir`` name.

    The segment index counts finished segments in log order, so it matches the
    driver's own A, B, C ... labelling even when a segment failed before it
    could write a report.
    """

    attribution: dict[str, DriveAttribution] = {}
    for log in logs:
        index = 0
        for event in log.events:
            if event.get("event") != "segment_finished":
                continue
            index += 1
            run_id = _run_id_from_dir(event.get("run_dir"))
            if run_id is None:
                continue
            label = event.get("segment")
            attribution[run_id] = DriveAttribution(
                drive_id=log.drive_id,
                task=log.task,
                segment_index=index,
                segment_label=label if isinstance(label, str) else None,
            )
    return attribution


def load_energy_constants(path: Path) -> dict[str, Any]:
    """EnergyModel's constants as the design extraction recorded them.

    Reproduce with::

        python -c "import json;d=json.load(open(
            'docs/projects/game-observatory/cases/romantic-restaurant/design/energy-items.json',
            encoding='utf-8'));print(d['facts']['constants'],d['facts']['constants_source'])"

    The recovery clock is a constant in the decompiled ``EnergyModel``, not a
    row in any config table, so this product is where it is read from.
    """

    if not path.is_file():
        return {}
    try:
        payload = read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    facts = payload.get("facts") if isinstance(payload, dict) else None
    if not isinstance(facts, dict):
        return {}
    constants = facts.get("constants")
    if not isinstance(constants, dict):
        return {}
    return {
        "constants": constants,
        "source": facts.get("constants_source"),
        "product": path.name,
    }


@dataclass(frozen=True)
class SaveReadProbe:
    """One save-read latency probe file, wherever under feasibility it sits.

    F1-04 writes its live probes into per-window directories rather than into a
    single flat report, so the scan looks for the file names those windows use
    instead of assuming one path.  A probe still being written is skipped and
    counted; nothing here rewrites the facility line's files.
    """

    path: Path
    payload: dict[str, Any]
    relative: str


PROBE_GLOBS = (
    "F1-04-live-*.json",
    "F1-04-live-*/live_read_probe.json",
    "F1-04-live-*/*/live_read_probe.json",
    "F1-04-live-*/*/live_acceptance.json",
)


def load_save_read_probes(feasibility_dir: Path) -> tuple[list[SaveReadProbe], int]:
    """Every F1-04 live probe on disk, and how many were unreadable."""

    if not feasibility_dir.is_dir():
        return [], 0
    probes: list[SaveReadProbe] = []
    unreadable = 0
    seen: set[Path] = set()
    for pattern in PROBE_GLOBS:
        for path in sorted(feasibility_dir.glob(pattern)):
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                continue
            seen.add(resolved)
            try:
                payload = read_json(path)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                unreadable += 1
                continue
            if not isinstance(payload, dict):
                unreadable += 1
                continue
            probes.append(
                SaveReadProbe(
                    path=path,
                    payload=payload,
                    relative=path.relative_to(feasibility_dir).as_posix(),
                )
            )
    return probes, unreadable


def declared_apm_windows(reports: list[Report]) -> dict[str, str]:
    """Runs an aggregate report names as its formal APM retest, by run id.

    No segment report writes ``apm_window`` on itself.  What does exist is an
    aggregate report naming the one segment it measured the action rate in --
    F4-08 writes ``apm_retest.report`` beside the file's sha256 -- and that is
    the same declaration, made one level up.  Resolving it to a run id lets the
    gate separate the segment that was run to measure the rate from those that
    merely happened to start with energy.

    Reproduce with::

        python -c "import json;d=json.load(open(
            'data/domains/game_observatory/game_models/romantic-restaurant/'
            'feasibility/F4-08-20260906T202514Z.json',encoding='utf-8'));
            print(d['apm_retest']['report'])"
    """

    by_name = {report.name: report for report in reports}
    declared: dict[str, str] = {}
    for report in reports:
        retest = report.payload.get("apm_retest")
        if not isinstance(retest, dict):
            continue
        named = retest.get("report")
        if not isinstance(named, str) or not named:
            continue
        target = by_name.get(PurePath(named.replace("\\", "/")).name)
        run_id = target.run_id if target is not None else None
        if run_id:
            declared[run_id] = report.name
    return declared


def newest_report(reports: list[Report], schema: str) -> Report | None:
    """The latest report of one schema, by file name.

    The report names carry their capture stamp, so the last name in sort order
    is the newest revision of that product.
    """

    matching = [report for report in reports if report.payload.get("schema") == schema]
    if not matching:
        return None
    return sorted(matching, key=lambda report: report.name)[-1]


def load_navigation(path: Path) -> dict[str, Any]:
    """The surveyed navigation whitelist, as JSON.

    Reproduce with::

        python -c "import json;print(sorted(json.load(open(
            'config/game_observatory/romantic_restaurant/navigation.v1.json',
            encoding='utf-8'))['surfaces']))"

    ``SurfaceRegistry`` is not instantiated here: the audit only needs the
    registration text, and building the registry compiles every anchor.
    """

    if not path.is_file():
        return {}
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


@dataclass
class Inputs:
    """Everything a build needs, already read from disk."""

    paths: dict[str, Path]
    reports: list[Report]
    index_entries: dict[str, IndexEntry]
    fact_counts: Counter[str] = field(default_factory=Counter)
    fact_horizon: str = ""
    citations: dict[str, list[Citation]] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    drive_logs: list[DriveLog] = field(default_factory=list)
    drive_attribution: dict[str, DriveAttribution] = field(default_factory=dict)
    energy_constants: dict[str, Any] = field(default_factory=dict)
    navigation: dict[str, Any] = field(default_factory=dict)
    comparison: Report | None = None
    projection: Report | None = None
    multi_day: Report | None = None
    latency: Report | None = None
    save_read_probes: list[SaveReadProbe] = field(default_factory=list)
    apm_retests: dict[str, str] = field(default_factory=dict)
    crossref_facts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def reports_by_schema(self, schema: str) -> list[Report]:
        return [report for report in self.reports if report.payload.get("schema") == schema]


_CITATION_SECTIONS = (
    ("attempts", "attempt", "attempt"),
    ("segments", "segment", "segment"),
    ("retest_segments", "segment", "segment"),
)


def collect_citations(reports: list[Report]) -> dict[str, list[Citation]]:
    """Find aggregate reports that summarise runs reported elsewhere.

    The convergence and long-run reports list their attempts and segments by
    ``run_id``; those runs already have their own per-run report, so the
    citation only records which aggregate view a run belongs to.
    """

    citations: dict[str, list[Citation]] = {}
    for report in reports:
        for container in _walk_dicts(report.payload):
            for key, role, label_key in _CITATION_SECTIONS:
                rows = container.get(key)
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    run_id = row.get("run_id")
                    if not isinstance(run_id, str):
                        continue
                    label = str(row.get(label_key, "")) or run_id
                    citations.setdefault(run_id, []).append(
                        Citation(report=report.name, role=role, label=label)
                    )
    return citations


def _walk_dicts(value: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    if depth > 2 or not isinstance(value, dict):
        return
    yield value
    for child in value.values():
        yield from _walk_dicts(child, depth + 1)


def load_inputs(paths: dict[str, Path] | None = None) -> Inputs:
    resolved = paths or default_paths()
    reports = list(iter_reports(resolved["feasibility"]))
    summary_path = resolved["summary"]
    summary = read_json(summary_path) if summary_path.is_file() else {}
    fact_counts, fact_horizon = count_facts_by_report(resolved["facts"])
    drive_logs = load_drive_logs(resolved["feasibility"])
    probes, _ = load_save_read_probes(resolved["feasibility"])
    return Inputs(
        paths=resolved,
        reports=reports,
        index_entries=load_index_entries(replay_roots(resolved)),
        fact_counts=fact_counts,
        fact_horizon=fact_horizon,
        citations=collect_citations(reports),
        summary=summary if isinstance(summary, dict) else {},
        drive_logs=drive_logs,
        drive_attribution=drive_attribution(drive_logs),
        energy_constants=load_energy_constants(resolved["energy_constants"]),
        navigation=load_navigation(resolved["navigation"]),
        comparison=newest_report(reports, COMPARISON_SCHEMA),
        projection=newest_report(reports, PROJECTION_SCHEMA),
        multi_day=newest_report(reports, MULTI_DAY_SCHEMA),
        latency=newest_report(reports, LATENCY_SCHEMA),
        save_read_probes=probes,
        apm_retests=declared_apm_windows(reports),
        crossref_facts=load_crossref_facts(resolved["facts"]),
    )
