# [OMNI] origin=omnicompany domain=utility/feishu_wiki_pull ts=2026-07-22T14:09:05Z type=package status=active
# [OMNI] summary="隔离鉴权并完整拉取collab platform Wiki 空间的可复用设施"
# [OMNI] why="Walker 需要从collab platform一手资料反查设定，同时不得切换或污染本机 lark-cli 登录态"
# [OMNI] tags=feishu,wiki,pull,provenance,walker
"""Feishu Wiki pull facility with no dependency on local lark-cli state."""

from .client import FeishuApiError, FeishuClient, WikiNode
from .puller import PullResult, WikiPuller

__all__ = ["FeishuApiError", "FeishuClient", "PullResult", "WikiNode", "WikiPuller"]
