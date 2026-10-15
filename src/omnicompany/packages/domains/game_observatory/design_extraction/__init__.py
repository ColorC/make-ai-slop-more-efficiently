"""Configuration-derived reverse-design documents and their verified layer."""

from .config_facts import (
    ConfigFacts,
    extract_all,
    extract_board_initial_layout,
    extract_economy_item_chains,
    extract_economy_order_pricing,
    extract_monetization_shop_iap_cn,
    extract_progression_main_tasks,
    extract_progression_player_levels,
    parse_rewards,
)
from .coverage_inventory import (
    INVENTORY_SLUG,
    build_coverage_inventory,
    count_layers,
    render_coverage_inventory,
)
from .crossref_ledger import (
    build_crossref_ledger,
    matrix_field_name,
    matrix_tables,
    write_crossref_ledger,
)
from .evidence import EvidenceBundle, parse_markdown_tables, parse_registry
from .live_facts import LIVE_EXTRACTORS, extract_live_all, parse_bi_events
from .permissive_facts import (
    PERMISSIVE_EXTRACTORS,
    decode_close_condition,
    group_save_tables,
    live_progress,
    parse_energy_constants,
)
from .render_docs import (
    build_document_payloads,
    foreign_documents,
    render_index,
    render_markdown,
    write_design_documents,
)

__all__ = [
    "ConfigFacts",
    "EvidenceBundle",
    "INVENTORY_SLUG",
    "LIVE_EXTRACTORS",
    "PERMISSIVE_EXTRACTORS",
    "build_coverage_inventory",
    "build_crossref_ledger",
    "build_document_payloads",
    "count_layers",
    "decode_close_condition",
    "extract_all",
    "extract_board_initial_layout",
    "extract_economy_item_chains",
    "extract_economy_order_pricing",
    "extract_live_all",
    "extract_monetization_shop_iap_cn",
    "extract_progression_main_tasks",
    "extract_progression_player_levels",
    "group_save_tables",
    "live_progress",
    "matrix_field_name",
    "matrix_tables",
    "parse_bi_events",
    "parse_energy_constants",
    "parse_markdown_tables",
    "parse_registry",
    "parse_rewards",
    "foreign_documents",
    "render_coverage_inventory",
    "render_index",
    "render_markdown",
    "write_crossref_ledger",
    "write_design_documents",
]
