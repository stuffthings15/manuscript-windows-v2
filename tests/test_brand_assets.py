from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MANUSCRIPT_ROOT = ROOT / "Manuscript" if (ROOT / "Manuscript").is_dir() else ROOT.parent


def test_manuscript_brand_assets_are_large_and_high_contrast() -> None:
    svg = (ROOT / "resources" / "manuscript.svg").read_text(encoding="utf-8")
    assert 'viewBox="0 0 1024 1024"' in svg
    assert "#17120D" in svg
    assert "#D6A85F" in svg
    assert "#F2D6A2" in svg
    assert "#3B291C" in svg

    image = Image.open(ROOT / "resources" / "manuscript.png").convert("RGBA")
    assert image.size == (1024, 1024)
    assert image.getbbox() == (0, 0, 1024, 1024)

    for size in (16, 24, 32, 68):
        shell_icon = image.resize((size, size), Image.Resampling.LANCZOS)
        pixels = list(shell_icon.get_flattened_data())
        total = size * size
        assert sum(alpha > 220 for _red, _green, _blue, alpha in pixels) >= total * 0.9
        assert sum(
            alpha > 220 and (red + green + blue) / 3 > 150
            for red, green, blue, alpha in pixels
        ) >= total * 0.18
        assert sum(
            alpha > 220 and (red + green + blue) / 3 < 70
            for red, green, blue, alpha in pixels
        ) >= total * 0.25


def test_windows_icon_contains_every_required_shell_size() -> None:
    icon = Image.open(ROOT / "resources" / "manuscript.ico")
    assert icon.ico.sizes() == {
        (16, 16),
        (24, 24),
        (32, 32),
        (48, 48),
        (64, 64),
        (128, 128),
        (256, 256),
    }


def test_android_launcher_uses_the_same_high_contrast_mark() -> None:
    manifest = (
        MANUSCRIPT_ROOT / "Android" / "app" / "src" / "main" / "AndroidManifest.xml"
    ).read_text(encoding="utf-8")
    vector = (
        MANUSCRIPT_ROOT
        / "Android"
        / "app"
        / "src"
        / "main"
        / "res"
        / "drawable"
        / "ic_manuscript.xml"
    ).read_text(encoding="utf-8")
    assert 'android:icon="@mipmap/ic_launcher"' in manifest
    assert 'android:roundIcon="@mipmap/ic_launcher_round"' in manifest
    assert "#17120D" in vector
    assert "#D6A85F" in vector
    assert "#F2D6A2" in vector
    assert "#3B291C" in vector


def test_desktop_header_uses_a_large_logo() -> None:
    source = (ROOT / "src" / "echo_link_app" / "window.py").read_text(encoding="utf-8")
    assert "icon.setFixedSize(72, 72)" in source
    assert '.pixmap(68, 68)' in source
