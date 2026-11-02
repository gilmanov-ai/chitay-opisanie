#!/usr/bin/env python3
"""Сборка ролика «Читай описание»: подложка + заголовок в выбранном стиле.

python3 build.py --video in.mp4 --title "текст" --style insta|box --out out.mp4
           [--start 0] [--dur 7] [--rotate cw|ccw]
Пустая строка в заголовке ("\n\n") = новый абзац (отдельный блок).
"""
import argparse, json, os, subprocess, tempfile
import cv2
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
# Безопасная зона Reels: сверху шапка, снизу ник/описание, справа кнопки
SAFE_TOP, SAFE_BOTTOM = 260, 1450
SAFE_LEFT, SAFE_RIGHT = 70, 960

HERE = os.path.dirname(os.path.abspath(__file__))
STYLES = {
    # 1. Инстаграмная плашка: каждая строка на своей скруглённой белой плашке
    "insta": dict(font=os.path.join(HERE, "fonts/Inter-Bold.otf"), size=46,
                  max_w=640, line_h=60, pad_x=20, radius=14, para_gap=34),
    # 2. Белый прямоугольник, текст Futura-подобным шрифтом
    "box": dict(font=os.path.join(HERE, "fonts/Jost-600.ttf"), size=64,
                max_w=870, line_h=72, pad_x=40, pad_y=28, para_gap=0),
    # 3. Затемнённая подложка + белый текст без плашки
    "dark": dict(font=os.path.join(HERE, "fonts/Inter-SemiBold.otf"), size=54,
                 max_w=700, line_h=66, para_gap=40, dim=0.45),
    # 3б. То же, но жирный курсив
    "dark_italic": dict(font=os.path.join(HERE, "fonts/Inter-BoldItalic.otf"), size=50,
                        max_w=760, line_h=62, para_gap=36, dim=0.45),
}


def wrap(text, font, max_w):
    # короткие предлоги/союзы («в», «и», «не», «на»…) не оставляем в конце строки
    raw, words = text.split(), []
    for w in raw:
        if words and w in ("—", "–"):  # тире не начинает строку
            words[-1] += "\u00a0" + w
        elif words and len(words[-1].split("\u00a0")[-1].strip("«»\"'(")) <= 2 and words[-1][-1].isalpha():
            words[-1] += "\u00a0" + w
        else:
            words.append(w)
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if font.getlength(t) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    # не оставляем одно слово висеть в последней строке
    if len(lines) > 1 and len(lines[-1].split()) == 1:
        prev = lines[-2].split(" ")
        if len(prev) > 2:
            lines[-2] = " ".join(prev[:-1]); lines[-1] = prev[-1] + " " + lines[-1]
    return lines


def layout(title, st):
    font = ImageFont.truetype(st["font"], st["size"])
    paras = [p.strip() for p in title.replace("\r", "").split("\n\n") if p.strip()]
    if st is STYLES["box"]:
        paras = [" ".join(paras)]
    blocks = [wrap(" ".join(p.split()), font, st["max_w"]) for p in paras]
    n = sum(len(b) for b in blocks)
    h = n * st["line_h"] + (len(blocks) - 1) * st["para_gap"] + 2 * st.get("pad_y", 8)
    return font, blocks, h


def render_overlay(title, style, y_top, path):
    st = STYLES[style]
    font, blocks, h = layout(title, st)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = W // 2  # по центру экрана, как в залетевших роликах
    lh = st["line_h"]
    if "dim" in st:
        from PIL import ImageFilter
        img = Image.new("RGBA", (W, H), (0, 0, 0, int(255 * st["dim"])))
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sd = ImageDraw.Draw(sh)
        txt = Image.new("RGBA", (W, H), (0, 0, 0, 0)); td = ImageDraw.Draw(txt)
        y = y_top + 8
        for b in blocks:
            for l in b:
                sd.text((cx + 2, y + lh / 2 + 3), l, font=font, fill=(0, 0, 0, 150), anchor="mm")
                td.text((cx, y + lh / 2), l, font=font, fill="white", anchor="mm"); y += lh
            y += st["para_gap"]
        img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(6)))
        img = Image.alpha_composite(img, txt)
        img.save(path)
        return h
    if style == "box":
        lines = blocks[0]
        tw = max(font.getlength(l) for l in lines)
        bw = tw + 2 * st["pad_x"]
        d.rectangle([cx - bw / 2, y_top, cx + bw / 2, y_top + h], fill="white")
        y = y_top + st["pad_y"]
        for l in lines:
            d.text((cx, y + lh / 2), l, font=font, fill="black", anchor="mm"); y += lh
    else:
        y = y_top + st.get("pad_y", 8)
        for b in blocks:
            for l in b:  # плашки
                lw = font.getlength(l) + 2 * st["pad_x"]
                d.rounded_rectangle([cx - lw / 2, y - 2, cx + lw / 2, y + lh + 2],
                                    radius=st["radius"], fill="white")
                y += lh
            y += st["para_gap"]
        y = y_top + st.get("pad_y", 8)
        for b in blocks:
            for l in b:
                d.text((cx, y + lh / 2 + 1), l, font=font, fill="black", anchor="mm"); y += lh
            y += st["para_gap"]
    img.save(path)
    return h


def faces(video, start, dur, vf):
    """Объединённая рамка лиц по нескольким кадрам (в координатах 1080x1920)."""
    det = cv2.CascadeClassifier(os.path.join(HERE, "face.xml"))
    boxes = []
    with tempfile.TemporaryDirectory() as t:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-t", str(dur),
                        "-i", video, "-vf", vf + ",fps=1.5", os.path.join(t, "f%02d.jpg")], check=True)
        for f in sorted(os.listdir(t)):
            g = cv2.cvtColor(cv2.imread(os.path.join(t, f)), cv2.COLOR_BGR2GRAY)
            found = det.detectMultiScale(g, 1.1, 8, minSize=(160, 160))
            if len(found):  # берём самое крупное лицо в кадре
                x, y, w, h = max(found, key=lambda r: r[2] * r[3])
                boxes.append((int(x), int(y), int(x + w), int(y + h)))
    if not boxes:
        return None
    med = lambda i: sorted(b[i] for b in boxes)[len(boxes) // 2]
    return (med(0), med(1), med(2), med(3))


def pick_y(h, face):
    """Ставим текст в безопасной зоне так, чтобы не перекрыть лицо.
    Предпочтение — чуть ниже центра, как в залетевших роликах."""
    cands = list(range(SAFE_TOP, SAFE_BOTTOM - h + 1, 20))
    if not cands:
        return SAFE_TOP
    pref = int(H * 0.56 - h / 2)
    def cost(y):
        c = abs(y - pref)
        if face:
            # бережём глаза и рот; подбородок и грудь можно перекрывать
            fh = face[3] - face[1]
            fy0, fy1 = face[1] - 30, face[1] + int(fh * 0.85)
            ov = max(0, min(y + h, fy1) - max(y, fy0))
            c += ov * 50
        return c
    return min(cands, key=cost)


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--video", required=True); a.add_argument("--title", required=True)
    a.add_argument("--style", default="insta", choices=STYLES); a.add_argument("--out", required=True)
    a.add_argument("--start", type=float, default=0); a.add_argument("--dur", type=float, default=7)
    a.add_argument("--rotate", choices=["cw", "ccw"])
    p = a.parse_args()
    rot = {"cw": "transpose=1,", "ccw": "transpose=2,"}.get(p.rotate, "")
    vf = f"{rot}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
    face = faces(p.video, p.start, p.dur, vf)
    _, _, h = layout(p.title, STYLES[p.style])
    y = pick_y(h, face)
    with tempfile.TemporaryDirectory() as t:
        ov = os.path.join(t, "ov.png")
        render_overlay(p.title, p.style, y, ov)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(p.start), "-t", str(p.dur),
                        "-i", p.video, "-i", ov, "-filter_complex",
                        f"[0:v]{vf}[b];[b][1:v]overlay=0:0,format=yuv420p",
                        "-an", "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                        "-movflags", "+faststart", p.out], check=True)
    print(json.dumps({"out": p.out, "text_y": y, "face": face}))


if __name__ == "__main__":
    main()
