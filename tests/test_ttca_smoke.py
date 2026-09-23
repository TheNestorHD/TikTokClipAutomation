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
    "_parse_transcription_response",
    "_clip_category_name",
    "is_just_chatting_clip",
    "pick_just_chatting_gameplay",
    "_parse_tiktok_draft_count_texts",
    "just_chatting_top_height",
    "_format_transcription_for_omni",
    "_clip_channel_name",
    "_ensure_omni_identity_hashtags",
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

    # Contratos de arquitectura: Whisper es el único transcriptor y las
    # operaciones externas/intermedias deben poder quedar bloqueadas tras un crash.
    assert "def transcribe_audio_with_omni" not in source
    assert "TRANSCRIPTION_MODEL" not in source
    assert "Fallback directo a Nemotron Omni" not in source
    assert "upload_interrupted" in source
    assert ".part.mp4" in source
    assert "TÍTULO ORIGINAL DEL CLIP EN KICK:" in source
    assert "CATEGORÍA DEL CLIP:" in source
    assert "TRANSCRIPCIÓN GENERADA EXCLUSIVAMENTE POR WHISPER:" in source
    assert 'button[data-e2e="save_draft_button"]:visible' in source
    assert 'btn.wait_for(state="visible", timeout=12000)' not in source
    assert 'draft_selector = \'button[data-e2e="save_draft_button"]\'' in source
    assert 'draft_btn.dispatch_event("click", timeout=2000)' in source
    assert 'draft_btn.click(timeout=4000, force=True, no_wait_after=True)' in source
    assert 'save_network_events = []' in source
    assert 'REQUEST FAILED' in source
    assert 'draft_btn.evaluate("(el) => el.click()")' in source
    assert 'page.mouse.move(x, y)' in source
    assert 'page.mouse.down()' in source
    assert 'page.mouse.up()' in source
    assert 'page.keyboard.insert_text(caption)' in source
    assert 'desc.inner_text(timeout=1000)' in source
    assert 'Forzamos blur' in source
    assert '_wait_for_tiktok_upload_quiet' in source
    assert 'draft_save_success = threading.Event()' in source
    assert 'post_draft/save' in source
    assert 'Borrador aceptado por TikTok' in source
    assert 'Subidas internas de TikTok estabilizadas' in source
    assert 'TIKTOK_AUTO_UPLOAD = env_bool("TIKTOK_AUTO_UPLOAD", False)' in source
    assert 'self.auto_upload_var = tk.BooleanVar(value=False)' in source
    assert 'https://www.tiktok.com/tiktokstudio/content?tab=draft' in source
    assert 'Header_HeaderTabBar_Container' in source
    assert 'contadores encontrados en TikTok' in source
    assert 'r"^\s*(?:Drafts|Borradores)' in source
    assert 'TIKTOK_AUTO_UPLOAD = env_bool("TIKTOK_AUTO_UPLOAD", False)' in source
    assert '(?:Drafts|Borradores)' in source
    assert 'self.draft_capacity_blocked' in source
    assert 'self.root.after(0, self._drain_output_queue)' in source
    assert '("pipeline", "⚡", "Pipeline")' not in source
    assert 'REQUEST FAILED' in source

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
    assert namespace["_parse_tiktok_draft_count_texts"](["Posts 34", "Drafts 0"]) == [(0, "Drafts 0")]\n    assert namespace["_parse_tiktok_draft_count_texts"](["Posts 34", "Drafts: 10", "Borradores: 10"]) == [(10, "Drafts: 10"), (10, "Borradores: 10")]\n    assert namespace["_clip_channel_name"]({
        "channel": {
            "id": 45443815,
            "username": "clockerr",
            "slug": "clockerr",
            "profile_picture": "https://example.invalid/user.webp",
        }
    }) == "clockerr"

    safe_caption = namespace["_ensure_omni_identity_hashtags"](
        "Una reacción graciosa #humor #rioplatense",
        "clockerr",
    )
    assert safe_caption.endswith("#clockerr #kick")
    assert len(safe_caption) <= 220
    assert all(
        not (token.startswith("#") and len(token) > 40)
        for token in safe_caption.split()
    )
    parsed = namespace["_parse_transcription_response"](
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

    formatted = namespace["_format_transcription_for_omni"]([
        {"word": "hola", "start": 1.25, "end": 1.60},
        {"word": "mundo", "start": 1.61, "end": 2.05},
    ])
    assert "[001.25-001.60] hola" in formatted
    assert "[001.61-002.05] mundo" in formatted

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
