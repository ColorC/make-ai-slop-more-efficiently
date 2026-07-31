---
name: gameplay-discovery
description: Derive evidence-backed gameplay or business candidates from observed states, transitions, resources, progression, and neighboring systems. Use when enough interaction evidence exists to propose a gameplay boundary for later design reconstruction.
---

# Gameplay Discovery

Contract version: `1.0.0`.

Produce structured candidates for the Game > Gameplay hierarchy. Keep interpretation separate from recorded game behavior.

## Close a candidate

1. Identify the entry condition and entry state.
2. Link the main states and player operations in order, including what the player sees, clicks or selects, and what the system displays or changes.
3. Record rules indicated by repeated outcomes, resources consumed or produced, progression axes, limits, unlocks, failure states, exits, and connections to neighboring gameplay.
4. Attach every claim to original runs, steps, screenshots, UI trees, or source records.
5. Mark the boundary as a candidate until the entry, main states, key transitions, resource or progression clue, and exit or adjacency are all present.

Navigation-only surfaces and unrelated destinations stay as connected gameplay, not members of the candidate. Homogeneous variants share one interface family while retaining all screenshots.

Append candidates through the canonical `GameplayCandidateV1` writer and coverage gaps through `FrontierTaskV1`. Publishing a reverse-designed gameplay document requires the separate content pipeline and human-readable reconstruction.
