"""Generate PNG PWA icons from scratch using Pillow (no SVG renderer needed)."""
from django.core.management.base import BaseCommand
from django.conf import settings
import os


class Command(BaseCommand):
    help = 'Generate PWA icon PNGs (192×192, 512×512, 180×180 apple-touch-icon)'

    def handle(self, *args, **options):
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            self.stderr.write('Pillow is required: pip install Pillow')
            return

        out_dir = os.path.join(settings.BASE_DIR, 'nilm', 'static', 'images')
        os.makedirs(out_dir, exist_ok=True)

        for size, name in [(512, 'icon-512.png'), (192, 'icon-192.png'), (180, 'apple-touch-icon.png')]:
            img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            s = size
            r = s // 5  # corner radius for background

            # Rounded background
            draw.rounded_rectangle([0, 0, s, s], radius=r, fill=(11, 17, 32))

            pad = s * 0.12
            cx = s / 2

            # House walls polygon
            roof_y = s * 0.22
            wall_top_y = s * 0.44
            wall_bot_y = s * 0.82
            left_x = s * 0.19
            right_x = s * 0.81

            lw = max(3, s // 26)

            # Roof triangle
            draw.polygon(
                [(cx, roof_y), (left_x, wall_top_y), (right_x, wall_top_y)],
                outline=(0, 229, 201), fill=None, width=lw
            )
            # House body rectangle
            draw.rectangle(
                [left_x, wall_top_y, right_x, wall_bot_y],
                outline=(0, 229, 201), fill=None, width=lw
            )

            # Amber windows
            win_w = s * 0.14
            win_h = s * 0.13
            win_y = s * 0.50
            win_r = max(2, s // 60)
            draw.rounded_rectangle(
                [left_x + s * 0.04, win_y, left_x + s * 0.04 + win_w, win_y + win_h],
                radius=win_r, fill=(255, 149, 0)
            )
            draw.rounded_rectangle(
                [right_x - s * 0.04 - win_w, win_y, right_x - s * 0.04, win_y + win_h],
                radius=win_r, fill=(255, 149, 0)
            )

            # Door
            door_w = s * 0.16
            door_h = s * 0.22
            door_x = cx - door_w / 2
            door_y = wall_bot_y - door_h
            draw.rounded_rectangle(
                [door_x, door_y, door_x + door_w, wall_bot_y],
                radius=max(2, s // 50),
                outline=(0, 229, 201), fill=None, width=lw
            )

            # Teal peak dot
            dot_r = max(4, s // 26)
            draw.ellipse(
                [cx - dot_r, roof_y - dot_r, cx + dot_r, roof_y + dot_r],
                fill=(0, 229, 201)
            )

            path = os.path.join(out_dir, name)
            img.save(path, 'PNG')
            self.stdout.write(self.style.SUCCESS(f'Created {name} ({size}×{size})'))
