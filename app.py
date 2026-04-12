import os
import sys
import tempfile
import subprocess
from pathlib import Path

import streamlit as st
import whisper


# ----------------------------
# UI
# ----------------------------
st.set_page_config(page_title="Reel Subtitle Maker", layout="centered")

st.markdown("""
<style>
.block-container {
    max-width: 430px;
    padding-top: 1rem;
    padding-bottom: 4rem;
}
.stButton > button {
    width: 100%;
    height: 3rem;
    border-radius: 14px;
    font-size: 16px;
}
.stDownloadButton > button {
    width: 100%;
    height: 3rem;
    border-radius: 14px;
    font-size: 16px;
}
textarea, input, select {
    border-radius: 12px !important;
    font-size: 16px !important;
}
video {
    border-radius: 16px;
    width: 100% !important;
    height: auto !important;
}
</style>
""", unsafe_allow_html=True)


# ----------------------------
# Whisper
# ----------------------------
@st.cache_resource
def load_model(name: str):
    return whisper.load_model(name)


# ----------------------------
# 音声抽出
# ----------------------------
def extract_audio(video_path: str, audio_path: str):
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


# ----------------------------
# 動画長さ
# ----------------------------
def get_video_duration(video_path: str) -> float:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)

    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


# ----------------------------
# 文字起こし（英語固定 + segments）
# ----------------------------
def transcribe_with_whisper(audio_path: str, model_name: str):
    model = load_model(model_name)
    result = model.transcribe(
        audio_path,
        language="en",
        fp16=False
    )

    fillers = ["yeah", "you know"]

    cleaned_text = result["text"]
    for f in fillers:
        cleaned_text = cleaned_text.replace(f, "")
        cleaned_text = cleaned_text.replace(f.capitalize(), "")
    cleaned_text = " ".join(cleaned_text.split())

    cleaned_segments = []
    for seg in result.get("segments", []):
        seg_text = seg.get("text", "")
        for f in fillers:
            seg_text = seg_text.replace(f, "")
            seg_text = seg_text.replace(f.capitalize(), "")
        seg_text = " ".join(seg_text.split()).strip()

        if seg_text:
            new_seg = dict(seg)
            new_seg["text"] = seg_text
            cleaned_segments.append(new_seg)

    result["text"] = cleaned_text.strip()
    result["segments"] = cleaned_segments
    return result


# ----------------------------
# Edit の文を segments の timing に割り当てて SRT化
# ----------------------------
def make_srt_from_edit(edit_text: str, total_duration: float):
    def fmt(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t - int(t)) * 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    sentences = [s.strip() for s in edit_text.split(".") if s.strip()]
    if not sentences:
        return ""

    # 文の長さに応じて時間を配分
    lengths = [max(len(s), 1) for s in sentences]
    total_len = sum(lengths)

    srt = ""
    current = 0.0

    for i, sentence in enumerate(sentences):
        dur = total_duration * (lengths[i] / total_len)
        start = current
        end = current + dur
        current = end

        text = sentence
        if not text.endswith("."):
            text += "."

        srt += f"{i+1}\n"
        srt += f"{fmt(start)} --> {fmt(end)}\n"
        srt += f"{text}\n\n"

    return srt
# ----------------------------
# ffmpeg filter用にWindowsパス整形
# ----------------------------
def ffmpeg_path_for_filter(path: str) -> str:
    path = path.replace("\\", "/")
    if len(path) >= 2 and path[1] == ":":
        path = path[0] + "\\:" + path[2:]
    return path


# ----------------------------
# 字幕付き動画作成
# 見た目は今の設定維持
# ----------------------------
def make_video(video_path: str, srt_path: str, output_path: str, day_text: str, title_text: str):
    srt_for_filter = ffmpeg_path_for_filter(srt_path)

    font_bold = "C\\:/Windows/Fonts/arialbd.ttf"
    font_regular = "C\\:/Windows/Fonts/arial.ttf"

    temp_dir = Path(video_path).parent
    day_file = temp_dir / "day_overlay.txt"
    title_file = temp_dir / "title_overlay.txt"

    with open(day_file, "w", encoding="utf-8") as f:
        f.write(day_text)

    with open(title_file, "w", encoding="utf-8") as f:
        f.write(title_text)

    day_file_filter = ffmpeg_path_for_filter(str(day_file))
    title_file_filter = ffmpeg_path_for_filter(str(title_file))

    filter_complex = (
        "scale=720:-1,"
        "pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,"
        "drawbox=x=40:y=80:w=640:h=180:color=black@0.45:t=fill,"
        f"drawtext=fontfile='{font_bold}':textfile='{day_file_filter}':"
        "fontcolor=white:fontsize=35:x=(w-text_w)/2:y=280,"
        f"drawtext=fontfile='{font_regular}':textfile='{title_file_filter}':"
        "fontcolor=white:fontsize=46:x=(w-text_w)/2:y=360,"
        f"subtitles='{srt_for_filter}':force_style='Fontsize=10,Alignment=2,MarginV=67'"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", filter_complex,
        "-c:a", "copy",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result


# ----------------------------
# text保存
# ----------------------------
def save_text(path: Path, content: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ----------------------------
# state
# ----------------------------
defaults = {
    "video": "",
    "duration": 0.0,
    "raw": "",
    "edit": "",
    "segments": [],
    "output": "",
    "caption": "",
    "hashtags": "",
    "folder": "",
    "day_number": "",
    "title_text": "",
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ----------------------------
# UI
# ----------------------------
st.title("Reel Subtitle Maker")
st.caption("Create English subtitle videos for Instagram")

model = st.selectbox("Model", ["base", "small", "medium"], index=1)

st.text_input("Day number", key="day_number", placeholder="64")
st.text_input("Title", key="title_text", placeholder="My English practice")

file = st.file_uploader("Upload video", type=["mp4", "mov", "mkv", "avi", "webm"])

if file:
    suffix = Path(file.name).suffix or ".mp4"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file.read())
        video_path = tmp.name

    st.session_state.video = video_path

    try:
        st.session_state.duration = get_video_duration(video_path)
    except Exception as e:
        st.error(f"Could not read duration: {e}")

    st.video(video_path)

    if st.session_state.duration:
        st.write(f"Video duration: {round(st.session_state.duration, 2)} sec")

    if st.button("1) Transcribe"):
        try:
            audio_path = str(Path(video_path).with_suffix(".wav"))
            extract_audio(video_path, audio_path)

            result = transcribe_with_whisper(audio_path, model)

            text = result["text"].strip()
            segments = result.get("segments", [])

            st.session_state.raw = text
            st.session_state.edit = text
            st.session_state.segments = segments

            st.success("Transcription completed.")

        except Exception as e:
            st.error(f"Transcription failed: {e}")

st.subheader("2) Raw")
st.text_area("Raw transcript", key="raw", height=220)

st.subheader("3) Edit")
st.text_area("Edit by hand", key="edit", height=220)

if st.button("4) Create subtitles video"):
    try:
        video = st.session_state.video
        segs = st.session_state.segments
        edit_text = st.session_state.edit.strip()

        if not video:
            st.warning("Please upload a video first.")
        elif not segs:
            st.warning("Please transcribe first.")
        elif not edit_text:
            st.warning("No edit text.")
        else:
            day_number = st.session_state.day_number.strip()
            title_text = st.session_state.title_text.strip()

            day_label = f"Day {day_number}" if day_number else ""
            title_label = title_text if title_text else ""

            srt_text = make_srt_from_edit(edit_text, st.session_state.duration)
            srt_path = str(Path(video).with_suffix(".srt"))

            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(srt_text)

            output_path = str(Path(video).with_name(Path(video).stem + "_sub.mp4"))

            result = make_video(video, srt_path, output_path, day_label, title_label)

            if result.returncode != 0:
                st.error("FFmpeg error while creating subtitle video.")
                st.text(result.stderr)
            elif not os.path.exists(output_path):
                st.error("Subtitle video was not created.")
            else:
                st.session_state.output = output_path
                st.success("Subtitle video created.")

    except Exception as e:
        st.error(f"Subtitle creation failed: {e}")

if st.session_state.output and os.path.exists(st.session_state.output):
    with open(st.session_state.output, "rb") as f:
        st.download_button(
            "Download video",
            f,
            file_name="subtitle_video.mp4",
            mime="video/mp4"
        )

st.subheader("5) Caption")
st.text_area("Instagram caption", key="caption", height=150)

st.subheader("6) Hashtags")
st.text_area("Hashtags", key="hashtags", height=100)

if st.button("7) Save post package"):
    if not st.session_state.output:
        st.warning("Create video first.")
    else:
        try:
            folder = Path.home() / "Desktop" / "exports"
            folder.mkdir(exist_ok=True)

            base = Path(st.session_state.output).stem

            video_path = folder / f"{base}.mp4"
            caption_path = folder / f"{base}_caption.txt"
            hash_path = folder / f"{base}_hashtags.txt"
            post_path = folder / f"{base}_post.txt"
            meta_path = folder / f"{base}_meta.txt"

            with open(st.session_state.output, "rb") as src:
                video_bytes = src.read()

            with open(video_path, "wb") as dst:
                dst.write(video_bytes)

            save_text(caption_path, st.session_state.caption)
            save_text(hash_path, st.session_state.hashtags)

            full = st.session_state.caption.strip() + "\n\n" + st.session_state.hashtags.strip()
            save_text(post_path, full)

            meta_text = f"Day number: {st.session_state.day_number}\nTitle: {st.session_state.title_text}\n"
            save_text(meta_path, meta_text)

            st.session_state.folder = str(folder)

            st.success("Saved!")
            st.write(f"Saved to: {folder}")

        except Exception as e:
            st.error(f"Save failed: {e}")

if st.session_state.folder:
    st.subheader("8) Next step")
    st.write(f"Export folder: {st.session_state.folder}")
    st.write("1. Open the exports folder")
    st.write("2. Upload the subtitle video to Instagram")
    st.write("3. Open *_post.txt")
    st.write("4. Copy and paste it into your Instagram caption")

    if st.button("Open exports folder"):
        path = st.session_state.folder

        try:
            if not os.path.exists(path):
                st.error(f"Folder does not exist: {path}")
            else:
                st.success(f"Opening: {path}")
                if sys.platform.startswith("win"):
                    subprocess.run(["explorer", os.path.normpath(path)])
                elif sys.platform == "darwin":
                    subprocess.run(["open", path], check=True)
                else:
                    subprocess.run(["xdg-open", path], check=True)
        except Exception as e:
            st.error(f"Could not open folder: {e}")