"""Generate PNG PWA icons by rasterising icon.svg with cairosvg."""
import os
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = 'Generate PWA icon PNGs (192×192, 512×512, 180×180) from nilm/static/images/icon.svg'

    def handle(self, *args, **options):
        try:
            import cairosvg
        except ImportError:
            self.stderr.write(
                'cairosvg is required: pip install cairosvg\n'
                '  (also needs system libcairo — on macOS: brew install cairo)'
            )
            return

        svg_path = os.path.join(settings.BASE_DIR, 'nilm', 'static', 'images', 'icon.svg')
        if not os.path.exists(svg_path):
            self.stderr.write(f'icon.svg not found at {svg_path}')
            return

        out_dir = os.path.dirname(svg_path)
        for size, name in [(512, 'icon-512.png'), (192, 'icon-192.png'), (180, 'apple-touch-icon.png')]:
            out_path = os.path.join(out_dir, name)
            cairosvg.svg2png(url=svg_path, write_to=out_path, output_width=size, output_height=size)
            self.stdout.write(self.style.SUCCESS(f'Created {name} ({size}×{size})'))
