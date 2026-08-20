from pathlib import Path

from PIL import Image


desktop = Path(__file__).resolve().parent
source = desktop / "mio-icon.png"
target = desktop / "mio.ico"

with Image.open(source) as image:
    image.convert("RGBA").save(target, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

print(target)
