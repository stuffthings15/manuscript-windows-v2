from __future__ import annotations

from pathlib import Path
import sys

from PIL import Image
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "resources" / "manuscript.svg"
PNG = ROOT / "resources" / "manuscript.png"
ICO = ROOT / "resources" / "manuscript.ico"
PNG_SIZE = 1024
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> QImage:
    renderer = QSvgRenderer(str(SOURCE))
    if not renderer.isValid():
        raise RuntimeError(f"Invalid SVG: {SOURCE}")
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication([])
    del app
    PNG.parent.mkdir(parents=True, exist_ok=True)
    if not render(PNG_SIZE).save(str(PNG), "PNG"):
        raise RuntimeError(f"Could not write {PNG}")

    base = Image.open(PNG).convert("RGBA")
    frames = [base.resize((size, size), Image.Resampling.LANCZOS) for size in ICO_SIZES]
    frames[-1].save(
        ICO,
        format="ICO",
        append_images=frames[:-1],
        sizes=[(size, size) for size in ICO_SIZES],
    )
    print(f"wrote {PNG} ({PNG_SIZE}x{PNG_SIZE})")
    print(f"wrote {ICO} ({', '.join(map(str, ICO_SIZES))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
