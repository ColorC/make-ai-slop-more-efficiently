# [OMNI] origin=claude-code ts=2026-07-02T00:00:00Z type=cli summary="omni ledger — 留痕账本 CLI(append/tail/verify)" why="留痕账本骨架需要一个可从终端/管线直接调用的写入口,记录工具本身不接受路径参数(ledgers.yaml铁律),CLI是标准写入口之一" tags=ledger,cli,provenance
"""omni ledger —— 留痕账本(操作留痕唯一位置)命令行入口。

权威依据: docs/plans/[2026-07-02]SEMANTIC-OS-MAP/target-architecture.md 第 3.3 节。
存储登记: config/ledgers.yaml 的 ledger-ops 条目。

    追加一条: omni ledger append --type pipeline.run --agent claude-code --activity "跑了xx"
    看最近:   omni ledger tail -n 20
    裁决通过: omni ledger verify <事件id> --by claude-code --reason "人工核对通过"
"""

from __future__ import annotations

import json

import click

from .._access import any_caller


@click.group("ledger")
def cmd_ledger() -> None:
    """留痕账本:一条只追加的事件流(见 target-architecture.md 3.3 节)。"""


@cmd_ledger.command("append")
@click.option("--type", "event_type", default="generic", help="事件类型,如 pipeline.run / decision.applied")
@click.option("--agent", default="", help="执行者标识,如 claude-code / omni-cron / 人名")
@click.option("--activity", default="", help="人读一句话描述做了什么")
@click.option("--input", "inputs", multiple=True, help="输入引用(可多次), 统一引用字符串或路径")
@click.option("--output", "outputs", multiple=True, help="输出引用(可多次)")
@click.option("--consumed-decision", "consumed_decisions", multiple=True,
              help="本次运行读取并应用的历史裁决 id(可多次)")
@click.option("--meta", "meta_json", default="", help="附加元数据, JSON 字符串, 如 '{\"k\":\"v\"}'")
@click.option("--idempotency-key", default="", help="幂等键:同一 key 已存在则不重写, 直接返回已有事件id")
@any_caller
def cmd_ledger_append(event_type, agent, activity, inputs, outputs, consumed_decisions,
                       meta_json, idempotency_key) -> None:
    """追加一条留痕事件。"""
    from omnicompany.packages.services._core.ledger import LedgerEvent, append

    meta = {}
    if meta_json:
        try:
            meta = json.loads(meta_json)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"--meta 不是合法 JSON: {e}") from None
        if not isinstance(meta, dict):
            raise click.ClickException("--meta 必须是 JSON 对象(dict)")

    event = LedgerEvent(
        type=event_type,
        agent=agent,
        activity=activity,
        inputs=list(inputs),
        outputs=list(outputs),
        consumed_decisions=list(consumed_decisions),
        meta=meta,
    )
    eid = append(event, idempotency_key=idempotency_key or None)
    click.echo(eid)


@cmd_ledger.command("tail")
@click.option("-n", "count", default=20, show_default=True, help="取最近 n 条")
@any_caller
def cmd_ledger_tail(count: int) -> None:
    """看最近 n 条留痕事件(原始 JSON, 每行一条)。"""
    from omnicompany.packages.services._core.ledger import tail

    for rec in tail(count):
        click.echo(json.dumps(rec, ensure_ascii=False))


@cmd_ledger.command("verify")
@click.argument("event_id")
@click.option("--by", default="", help="谁做的裁决(agent 或人名)")
@click.option("--reason", default="", help="裁决理由")
@any_caller
def cmd_ledger_verify(event_id: str, by: str, reason: str) -> None:
    """把某条事件标记为已验证(追加一条 verdict.update 关联事件, 不改写原行)。"""
    from omnicompany.packages.services._core.ledger import set_verdict

    new_id = set_verdict(event_id, "verified", by=by, reason=reason)
    click.echo(new_id)


@cmd_ledger.command("deviate")
@click.option("--kind", type=click.Choice(["unmanaged", "missing", "modified", "undecidable"]),
              required=True,
              help="偏离类型:unmanaged=做了陈述库没覆盖的判断 / missing=该消费的陈述没消费 / "
                   "modified=实际做法与陈述相悖 / undecidable=无法判定")
@click.option("--note", required=True, help="一句话说明(偏离了什么、为什么)")
@click.option("--ref", "refs", multiple=True, help="被偏离的陈述 id(可多次)")
@click.option("--handling", type=click.Choice(["auto_correct", "alert_only", "report_only"]),
              default="alert_only", show_default=True, help="处置档:自动纠正/只告警等人/只报告")
@click.option("--mode", "change_mode", type=click.Choice(["active_change", "passive_correction"]),
              default="passive_correction", show_default=True,
              help="主动变更声明(做法应该变) vs 被动纠偏(回到陈述)")
@click.option("--agent", default="", help="报告者")
@click.option("--event", "related_event_id", default="", help="关联的运行事件 id")
@any_caller
def cmd_ledger_deviate(kind, note, refs, handling, change_mode, agent, related_event_id) -> None:
    """报告一笔偏离(显式动作;提议修订本体请另走 omni decisions candidate)。"""
    from omnicompany.packages.services._core.ledger import report_deviation

    eid = report_deviation(kind=kind, note=note, refs=list(refs), handling=handling,
                           change_mode=change_mode, agent=agent,
                           related_event_id=related_event_id)
    click.echo(eid)


@cmd_ledger.command("deviations")
@click.option("-n", "count", default=50, show_default=True, help="取最近 n 条")
@any_caller
def cmd_ledger_deviations(count: int) -> None:
    """列最近的偏离记录(候选流水线信号源之一:偏离聚集处=规则该修处)。"""
    from omnicompany.packages.services._core.ledger import list_deviations

    for rec in list_deviations(count):
        click.echo(json.dumps(rec, ensure_ascii=False))
