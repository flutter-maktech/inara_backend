"""
app/utils/location_utils.py
============================
Utility for automatically generating Google Maps links from plain-text
location strings.

Design principles
-----------------
- Zero external HTTP calls — uses the Google Maps Search URL which resolves
  on the client side. No API key required, no rate limits, no cost.
- Idempotent — if a valid map link is already provided, it is returned as-is.
- Pure functions — no side effects, fully testable.
- Used by both ClassesService and CoursesService via a single shared call.

Map URL strategy
----------------
Google Maps Search URL:
    https://www.google.com/maps/search/?api=1&query=<encoded_location>

This URL opens Google Maps and zooms to the best match for the query string.
It works for:
  - Named venues:   "INARA Studio A, West Bay, Doha"
  - Cities:         "Austin, Texas"
  - Full addresses: "123 Main St, New York, NY 10001"
  - Coordinates:    "25.3548,51.5326"  (passed straight through)
"""

from urllib.parse import quote


# ── Public helpers ─────────────────────────────────────────────────────────────

def build_map_link(location: str) -> str:
    """
    Generate a Google Maps Search URL from a plain-text location string.

    Args:
        location: Any human-readable place string, e.g.
                  "INARA Studio A, West Bay, Doha"
                  "Austin, Texas"
                  "25.3548,51.5326"

    Returns:
        A fully-formed Google Maps URL that opens the location in a map view.
    """
    encoded = quote(location.strip(), safe=",")
    return f"https://www.google.com/maps/search/?api=1&query={encoded}"


def resolve_map_link(location: str, existing_map_link: str | None) -> str:
    """
    Return an existing map link if valid, otherwise auto-generate one.

    This is the primary entry point used by both service layers.

    Args:
        location:         The plain-text location string (always present).
        existing_map_link: The caller-supplied map link (may be None or empty).

    Returns:
        A non-empty Google Maps URL — either the caller's own or auto-generated.

    Examples:
        resolve_map_link("Austin, Texas", None)
        → "https://www.google.com/maps/search/?api=1&query=Austin%2C+Texas"

        resolve_map_link("Austin, Texas", "https://maps.google.com/?q=30.2,-97.7")
        → "https://maps.google.com/?q=30.2,-97.7"   # caller's link preserved
    """
    if existing_map_link and existing_map_link.strip():
        return existing_map_link.strip()
    return build_map_link(location)