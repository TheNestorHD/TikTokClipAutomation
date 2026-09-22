import json
import os
import subprocess
import tempfile
from pathlib import Path

import tiktok_clip_automation as ttca


def run(cmd, cwd=None):
    result = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout)
    return result


def main():
    # Parser regression: Omni puede devolver campos estructurados.
    assert ttca._coerce_text({"caption": "hola"}) == "hola"
    parsed = ttca._parse_kimi_transcription_response(
        json.dumps(
            {
                "words": [
                    {"word": {"text": "hola"}, "start": 0, "end": 0.4},
                    {"word": "mundo", "start": 0.4, "end": 0.9},
                ]
            }
        )
    )
    assert [w["word"] for w in parsed] == ["hola", "mundo"]

    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        ttca.APP_DIR = work
        ttca.TARGET_W = 1080
        ttca.TARGET_H = 1920
        ttca.DIVIDER_H = 160
        ttca.DIVIDER_PATH = work / "divider.png"
        ttca.FONT_PATH = work / "missing.ttf"
        ttca.FFMPEG_EXE = "ffmpeg"
        ttca.FFMPEG_THREADS = 1

        main_video = work / "main.mp4"
        gameplay = work / "retention.mp4"
        divider = work / "divider.png"
        output = work / "output.mp4"
        ass = work / "smoke.ass"

        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(main_video),
        ])
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:size=320x180:rate=30:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(gameplay),
        ])
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=white:size=1080x160:duration=1",
            "-frames:v", "1", str(divider),
        ])

        ttca.create_ass_file([], ass, 1080, 1920, 900)
        ttca.prepare_fonts_dir()

        cmd = ttca.build_ffmpeg_cmd(
            main_video,
            output,
            None,
            320,
            180,
            ass,
            None,
            start_sec=0,
            end_sec=3,
            just_chatting=True,
            gameplay_path=gameplay,
        )
        run(cmd, cwd=work)

        width, height, fps, duration = ttca.get_video_info(output)
        assert (width, height) == (1080, 1920), (width, height)
        assert 2.5 <= duration <= 3.2, duration

        # Categoría API y selección aleatoria de retención.
        ttca.JUST_CHATTING_RETENTION_DIR = work / "retention"
        ttca.JUST_CHATTING_RETENTION_DIR.mkdir()
        retained = ttca.JUST_CHATTING_RETENTION_DIR / "game.mp4"
        retained.write_bytes(gameplay.read_bytes())
        assert ttca.is_just_chatting_clip(
            {"category": {"id": 15, "name": "Just Chatting", "slug": "just-chatting"}}
        )
        assert ttca.pick_just_chatting_gameplay() == retained

    print("TTCA smoke tests: OK")


if __name__ == "__main__":
    main()
