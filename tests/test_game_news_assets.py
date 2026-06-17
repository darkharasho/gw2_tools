"""The bundled source logos must exist and be valid PNGs so the embed
thumbnail (attachment://<logo>) renders for every registered source."""
from axitools.cogs.game_news import ASSETS_DIR, GameNewsCog


def test_every_source_logo_asset_is_present_and_png():
    for source in GameNewsCog.SOURCES:
        path = ASSETS_DIR / source.logo_asset
        assert path.exists(), f"missing logo asset for {source.key}: {path}"
        with path.open("rb") as handle:
            assert handle.read(8) == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
