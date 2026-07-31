# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:25Z
---
omnikb_type: kexp
id: kb.experiment.retired_network
name: 'Retired: network'
tags:
- topic.retired
- stage.abandoned
- retired_from.network
maturity: deprecated
summary: '`src/omnicompany/network/` held an unfinished federation prototype: - `peer_client.py`
  — PeerClient (Python side) - `remote_router.py` — RemoteRouter (dispatches to remote
  peer) - `omnicompany_pb2.py` + `omnicompany_pb2_grpc.py` — generated gRPC stubs
  - `convert.py` — proto <-...'
date_concluded: '2026-04-07'
method_summary: see _graveyard/network/
findings_summary:
- '原始位置: src/omnicompany/_graveyard/network'
- '退役理由: 见 _RETIRED.md (本 entry body 含全文)'
status: abandoned
---

# Retired: network

> Original location: `src/omnicompany/_graveyard/network/`
> Retired on: 2026-04-07

## Retirement notes (verbatim from _RETIRED.md)

# Retired 2026-04-07

`src/omnicompany/network/` held an unfinished federation prototype:
- `peer_client.py` — PeerClient (Python side)
- `remote_router.py` — RemoteRouter (dispatches to remote peer)
- `omnicompany_pb2.py` + `omnicompany_pb2_grpc.py` — generated gRPC stubs
- `convert.py` — proto <-> dict conversion

Zero production callers at retirement. Only references were in two demo
scripts (`scripts/demo_cross_lang.py`, `scripts/demo_visualizer.py`) which
are now also archived here for coherence. The sibling repo
`omnicompany-node-rs/` is the Rust side of this prototype.

If federation work resumes, revive this alongside the Rust node and write a
proper entry point (e.g. `omni peer connect <addr>`).

## What was lost

_(待补充: 这次退役放弃了什么能力? 是否有替代方案?)_

## Why it failed / was abandoned

_(待补充: 退役的根本原因, 不只是表面的 "no callers")_

## Lessons for future attempts

_(待补充: 如果将来重做类似系统应该注意什么?)_

## Resurrection conditions

_(待补充: 在什么情况下值得复活? 如本次 OmniKB 的复活就是一个例子)_

## Change log

- 2026-04-08 — auto-seeded from `_graveyard/network/_RETIRED.md`
