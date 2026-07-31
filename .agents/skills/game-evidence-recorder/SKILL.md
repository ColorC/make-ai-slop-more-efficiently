---
name: game-evidence-recorder
description: Record complete, source-pixel-reconstructable Before-Action-After evidence for every AI-player observation, action, skill, guide test, and gameplay claim. Use around every device operation and whenever evidence must support a public reconstruction.
---

# Game Evidence Recorder

Contract version: `1.0.0`.

Use `DeviceGateway`, EvidenceRecorder, ArtifactStore, ObservatoryStore, and AI-player evidence references.

## Record one step

1. Capture the full Before frame, UI tree when available, viewport, orientation, process state, environment identity, and capture time.
2. Persist the action intent, normalized action, source-pixel point, target bounds, target name, action start/end, and underlying ActionRun before interpreting the result.
3. Capture intermediate frames or video and the terminal After frame and UI tree. Apply scene-appropriate stability rules.
4. Run objective checks, then close EvidenceStep, EvidenceRun, and manifest. Verify every artifact path and SHA-256.
5. Link the evidence to state, transition, task, memory, skill run, guide feedback, and gameplay candidate records.

A step cannot succeed when its terminal image, action location, environment identity, objective check, file, or hash is missing. Keep failed and no-change evidence; it prevents repeated actions and false state creation.

The model may consume cropped or cached perception, while the evidence surface always retains the full original material.
