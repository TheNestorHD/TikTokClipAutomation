import ast
import json
import random
import shutil
import subprocess
import tempfile
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "tiktok_clip_automation.py"

FUNCTIONS = {
    "_coerce_text",
    "_clean_transcribed_words",
    "_parse_kimi_transcription_response",
    "_clip_category_name",
    "is_just_chatting_clip",
    "pick_just_chatting_gameplay",
    "just_chatting_top_height",
    "prepare_fonts_dir",
    "create_ass_file",
    "build_ffmpeg_cmd",
}


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
        raise AssertionError("Command failed:\n" + " ".join(map(str, cmd)) + "\n\n" + result.stdout)
    return result


def load_functions():
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SOURCE))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS
    ]
    namespace = {
        "__builtins__": __builtins__,
        "ast": ast,
        "json": json,
        "random": random,
        "shutil": shutil,
        "Path": Path,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


def main():
    namespace = load_functions()

    assert namespace["_coerce_text"]({"caption": "hola"}) == "hola"
    parsed = namespace["_parse_kimi_transcription_response"](
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
        ns = namespace
        ns.update({
            "APP_DIR": work,
            "TARGET_W": 1080,
            "TARGET_H": 1920,
            "DIVIDER_H": 160,
            "DIVIDER_PATH": work / "divider.png",
            "FONT_PATH": work / "missing.ttf",
            "FFMPEG_EXE": "ffmpeg",
            "FFMPEG_THREADS": 1,
            "SUB_SIZE": 128,
            "SUB_COLOR": "&H00FFFFFF",
            "SUB_BORDER": "&H00000000",
            "SUB_BORDER_WIDTH": 12,
            "SUB_MAX_WORDS": 1,
            "JUST_CHATTING_RETENTION_DIR": work / "retention",
            "JUST_CHATTING_VIDEO_EXTENSIONS": {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".ts", ".m2ts"},
        })

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

        ass_path = ass
        ns["create_ass_file"]([], ass_path, 1080, 1920, 900)
        ns["prepare_fonts_dir"]()

        cmd = ns["build_ffmpeg_cmd"](
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

        probe = run([
            "ffprobe", "-v", "error",
            "-show_entries", "stream=width,height",
            "-show_entries", "format=duration",
            "-of", "default=nw=1",
            str(output),
        ]).stdout
        assert "width=1080" in probe
        assert "height=1920" in probe
        duration_line = next(line for line in probe.splitlines() if line.startswith("duration="))
        duration = float(duration_line.split("=", 1)[1])
        assert 2.5 <= duration <= 3.2, duration

        ns["JUST_CHATTING_RETENTION_DIR"].mkdir()
        retained = ns["JUST_CHATTING_RETENTION_DIR"] / "game.mp4"
        shutil.copy2(gameplay, retained)
        assert ns["is_just_chatting_clip"](
            {"category": {"id": 15, "name": "Just Chatting", "slug": "just-chatting"}}
        )
        assert ns["pick_just_chatting_gameplay"]() == retained

    print("TTCA smoke tests: OK")


if __name__ == "__main__":
    main()
