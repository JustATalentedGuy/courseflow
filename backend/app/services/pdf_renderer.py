import os
import sys
import tempfile
from pathlib import Path


def _configure_windows_fontconfig() -> None:
    if os.name != "nt":
        return
    config_dir = Path(tempfile.gettempdir()) / "courseflow-fontconfig"
    cache_dir = config_dir / "cache"
    config_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    fonts_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    config_path = config_dir / "fonts.conf"
    config_path.write_text(
        (
            '<?xml version="1.0"?>\n'
            "<!DOCTYPE fontconfig SYSTEM \"fonts.dtd\">\n"
            "<fontconfig>\n"
            f"  <dir>{fonts_dir.as_posix()}</dir>\n"
            f"  <cachedir>{cache_dir.as_posix()}</cachedir>\n"
            "</fontconfig>\n"
        ),
        encoding="utf-8",
    )
    os.environ["FONTCONFIG_PATH"] = str(config_dir)
    os.environ["FONTCONFIG_FILE"] = config_path.name
    os.environ["XDG_CACHE_HOME"] = str(cache_dir)


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    _configure_windows_fontconfig()
    from weasyprint import HTML

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    HTML(filename=str(input_path), base_url=str(input_path.parent)).write_pdf(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
