import discord
from axitools.branding import BRAND_COLOUR, brand_embed


def test_brand_embed_defaults():
    embed = brand_embed(title="Hello")
    assert embed.title == "Hello"
    assert embed.colour == BRAND_COLOUR
    assert embed.footer.text == "Guild Wars 2 Tools"
    assert embed.description == ""


def test_brand_embed_overrides():
    embed = brand_embed(title="T", description="D", colour=discord.Colour.red())
    assert embed.description == "D"
    assert embed.colour == discord.Colour.red()
