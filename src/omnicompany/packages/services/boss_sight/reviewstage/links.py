"""Stable user-facing links for Reviewstage materials.

The raw ``/file`` endpoint is a carrier endpoint for renderers and downloads.
Human-facing links must enter the cockpit shell so Dockview can open the material
panel and select the registered renderer.
"""

from __future__ import annotations

from urllib.parse import quote


def review_material_open_path(material_id: str) -> str:
    """Return the cockpit/Dockview deep link for one review material."""

    encoded = quote(str(material_id).strip(), safe="")
    return f"/?open_type=review_material&open_id={encoded}"


def review_material_open_url(base_url: str, material_id: str) -> str:
    """Return an absolute cockpit/Dockview deep link."""

    return f"{base_url.rstrip('/')}{review_material_open_path(material_id)}"


__all__ = ["review_material_open_path", "review_material_open_url"]
