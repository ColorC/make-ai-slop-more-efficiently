# [OMNI] origin=codex domain=services/_governance/commit_steward ts=2026-08-03T00:45:00+08:00 type=router
# [OMNI] material_id="material:governance.commit_steward.post_commit_review.py"
"""Optional post-commit message review for local Git history.

``commit-run`` deliberately runs *after* an agent has understood, tested and
committed a coherent change.  It never scans a dirty worktree, stages files or
creates batches.  Instead it reads the complete committed diff for ``HEAD`` and
asks a low-cost reviewer whether the existing message should be kept.

Applying a suggestion is intentionally narrow: only the current local HEAD may
be amended, the index must be empty, and no known remote-tracking ref may
contain the commit.  Unstaged or untracked worktree changes are allowed because
they are not part of ``git commit --amend`` when the index is clean.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from omnicompany.core.config import omni_workspace_root


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report_dir() -> Path:
    path = omni_workspace_root() / "data" / "governance" / "commit_steward"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _repo_root() -> Path:
    return omni_workspace_root()


def _git(*args: str, check: bool = True) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=str(_repo_root()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail[:500]}")
    return process.stdout


@dataclass(frozen=True)
class CommitSnapshot:
    sha: str
    message: str
    files: tuple[str, ...]
    diff: str
    staged_paths: tuple[str, ...]
    remote_refs: tuple[str, ...]

    @property
    def subject(self) -> str:
        return self.message.splitlines()[0] if self.message else ""

    @property
    def safe_to_amend(self) -> bool:
        return not self.staged_paths and not self.remote_refs


@dataclass(frozen=True)
class CommitReview:
    verdict: str
    subject: str
    body: str
    reason: str

    @property
    def message(self) -> str:
        if self.verdict != "amend":
            return ""
        return f"{self.subject}\n\n{self.body}".rstrip() + "\n"


def _nonempty_lines(value: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in value.splitlines() if line.strip())


def inspect_commit(ref: str = "HEAD") -> CommitSnapshot:
    """Read one committed change completely; never inspect worktree diffs."""
    sha = _git("rev-parse", "--verify", ref).strip()
    message = _git("show", "-s", "--format=%B", sha).rstrip()
    files = _nonempty_lines(
        _git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha)
    )
    # No fixed prefix clipping: a reviewer either receives the complete
    # committed patch or the model call fails visibly and the agent reviews it.
    diff = _git("show", "--format=", "--find-renames", "--find-copies", sha)
    staged_paths = _nonempty_lines(_git("diff", "--cached", "--name-only"))
    remote_refs = _nonempty_lines(
        _git("branch", "-r", "--contains", sha, check=False)
    )
    return CommitSnapshot(
        sha=sha,
        message=message,
        files=files,
        diff=diff,
        staged_paths=staged_paths,
        remote_refs=remote_refs,
    )


_REVIEW_SYSTEM = """You review an existing local Git commit after it was created.
Read the complete committed diff and the existing commit message. Keep the
message when it is accurate and useful. Suggest an amendment only when it is
materially misleading, omits the commit's main purpose, or describes changes
that are not present. Do not invent tests, intent, files, or outcomes. Return
JSON only: {"verdict":"keep|amend","subject":"...","body":"...","reason":"..."}.
"""

_REVIEW_SCHEMA = {
    "type": "object",
    "required": ["verdict", "reason"],
    "properties": {
        "verdict": {"type": "string", "enum": ["keep", "amend"]},
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "reason": {"type": "string"},
    },
}

Reviewer = Callable[[str, str, dict[str, Any], str | None], dict[str, Any]]


def _default_reviewer(
    system: str,
    user: str,
    schema: dict[str, Any],
    model: str | None,
) -> dict[str, Any]:
    from omnicompany.runtime.llm.structured import call_json

    return call_json(
        system=system,
        user=user,
        schema=schema,
        model=model,
        caller="commit_steward.post_review",
        max_tokens=1200,
    )


def build_review_input(snapshot: CommitSnapshot) -> str:
    files = "\n".join(f"- {path}" for path in snapshot.files) or "(none)"
    return (
        f"Commit: {snapshot.sha}\n\n"
        f"Existing message:\n{snapshot.message}\n\n"
        f"Committed files:\n{files}\n\n"
        f"Complete committed diff:\n{snapshot.diff}"
    )


def review_commit(
    snapshot: CommitSnapshot,
    *,
    model: str | None = None,
    reviewer: Reviewer | None = None,
) -> CommitReview:
    """Review the complete committed patch and normalize the model decision."""
    raw = (reviewer or _default_reviewer)(
        _REVIEW_SYSTEM,
        build_review_input(snapshot),
        _REVIEW_SCHEMA,
        model,
    ) or {}
    verdict = str(raw.get("verdict") or "keep").strip().lower()
    reason = str(raw.get("reason") or "reviewer gave no reason").strip()
    subject_text = str(raw.get("subject") or "").strip()
    subject = subject_text.splitlines()[0] if subject_text else ""
    body = str(raw.get("body") or "").strip()
    if verdict != "amend" or not subject:
        return CommitReview("keep", snapshot.subject, "", reason)
    proposal = CommitReview("amend", subject, body, reason)
    if proposal.message.strip() == snapshot.message.strip():
        return CommitReview("keep", snapshot.subject, "", reason)
    return proposal


def amend_commit(snapshot: CommitSnapshot, review: CommitReview) -> dict[str, Any]:
    """Amend only an unpublished HEAD with an empty index."""
    if review.verdict != "amend":
        return {"amended": False, "reason": "review kept the existing message"}
    current_head = _git("rev-parse", "--verify", "HEAD").strip()
    if current_head != snapshot.sha:
        return {
            "amended": False,
            "blocked": True,
            "reason": "HEAD changed after review; run commit-run again",
        }
    staged_paths = _nonempty_lines(_git("diff", "--cached", "--name-only"))
    if staged_paths:
        return {
            "amended": False,
            "blocked": True,
            "reason": "index is not empty; refusing to mix staged changes into amend",
            "staged_paths": list(staged_paths),
        }
    remote_refs = _nonempty_lines(
        _git("branch", "-r", "--contains", snapshot.sha, check=False)
    )
    if remote_refs:
        return {
            "amended": False,
            "blocked": True,
            "reason": "commit is contained by a remote-tracking ref",
            "remote_refs": list(remote_refs),
        }

    message_path = report_dir() / f"_amend_{snapshot.sha[:12]}.txt"
    message_path.write_text(review.message, encoding="utf-8")
    try:
        _git("commit", "--amend", "-F", str(message_path))
    finally:
        message_path.unlink(missing_ok=True)
    new_sha = _git("rev-parse", "--verify", "HEAD").strip()
    return {
        "amended": True,
        "old_sha": snapshot.sha,
        "new_sha": new_sha,
    }


def run_commit(
    *,
    model: str | None = None,
    apply: bool = False,
    ref: str = "HEAD",
    reviewer: Reviewer | None = None,
) -> dict[str, Any]:
    """Review a completed commit and optionally amend its local message."""
    snapshot = inspect_commit(ref)
    review = review_commit(snapshot, model=model, reviewer=reviewer)
    applied = amend_commit(snapshot, review) if apply else {"amended": False}
    payload = {
        "generated_at": _now(),
        "mode": "post_commit_review",
        "model": model or "default",
        "commit": {
            "sha": snapshot.sha,
            "message": snapshot.message,
            "files": list(snapshot.files),
            "diff_chars": len(snapshot.diff),
            "safe_to_amend": snapshot.safe_to_amend,
            "staged_paths": list(snapshot.staged_paths),
            "remote_refs": list(snapshot.remote_refs),
        },
        "review": {
            "verdict": review.verdict,
            "subject": review.subject,
            "body": review.body,
            "reason": review.reason,
        },
        "applied": applied,
    }
    output = report_dir() / "commit_last.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["_written"] = str(output)
    return payload


__all__ = [
    "CommitReview",
    "CommitSnapshot",
    "amend_commit",
    "build_review_input",
    "inspect_commit",
    "review_commit",
    "run_commit",
]
