from pathlib import Path

import darkfly.catalog as catalog_mod


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_packaged_catalog_is_byte_identical_to_root():
    """The wheel ships darkfly/catalog.json; it must never drift
    from the root catalog.json that maintainers edit."""

    root = REPO_ROOT / "catalog.json"
    packaged = REPO_ROOT / "darkfly" / "catalog.json"

    assert packaged.is_file(), \
        "darkfly/catalog.json is missing (needed for packaged installs)"
    assert root.read_bytes() == packaged.read_bytes(), (
        "darkfly/catalog.json has drifted from catalog.json; "
        "copy the root catalog over it"
    )


def test_packaged_catalog_loads_and_validates():
    catalog = catalog_mod.load_catalog(
        REPO_ROOT / "darkfly" / "catalog.json")
    assert len(catalog) >= 58
