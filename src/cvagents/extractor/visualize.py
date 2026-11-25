"""Vẽ các khối bố cục (nhãn Docling, thứ tự đọc) lên ảnh trang để kiểm tra bằng mắt."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from cvagents.extractor.models import Block

LABEL_COLORS = {
    "title": (214, 39, 40), "section_header": (255, 127, 14), "text": (31, 119, 180),
    "list_item": (44, 160, 44), "table": (148, 103, 189), "picture": (140, 86, 75),
    "caption": (227, 119, 194), "key_value_region": (23, 190, 207),
}
OTHER_COLOR = (127, 127, 127)


def _font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def draw_blocks(image: Image.Image, page_size: tuple[float, float], blocks: list[tuple[int, Block]]) -> Image.Image:
    """blocks: (số thứ tự đọc, khối); toạ độ khối theo đơn vị trang, gốc trên-trái, được co giãn theo ảnh.

    Ảnh trả về có thêm một dải lề trắng phía trên để nhãn của khối sát mép trang không đè lên chữ.
    """
    s = image.width / page_size[0]
    font = _font(max(12, image.width // 42))  # theo bề rộng ảnh để còn đọc được khi ảnh thu nhỏ vào một cột
    pad = font.getbbox("Ag")[3] + 8 if blocks else 0
    base = Image.new("RGBA", (image.width, image.height + pad), (255, 255, 255, 255))
    base.paste(image.convert("RGBA"), (0, pad))
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    w = max(2, image.width // 400)
    for i, b in blocks:
        c = LABEL_COLORS.get(b.label, OTHER_COLOR)
        box = [b.x1 * s, b.y1 * s + pad, b.x2 * s, b.y2 * s + pad]
        d.rectangle(box, outline=c + (255,), fill=c + (28,), width=w)
        tag = f"{i}. {b.label}"
        _, _, tw, th = d.textbbox((0, 0), tag, font=font)
        y = max(box[1] - th - 4, 0)
        d.rectangle([box[0], y, box[0] + tw + 6, y + th + 4], fill=c + (235,))
        d.text((box[0] + 3, y + 1), tag, fill=(255, 255, 255, 255), font=font)
    return Image.alpha_composite(base, layer).convert("RGB")
