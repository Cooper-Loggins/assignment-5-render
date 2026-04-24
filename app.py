import io
import json
import os
import re
import urllib.request
import wave
from datetime import datetime
from functools import wraps
from hmac import compare_digest

from flask import Flask, Response, jsonify, render_template, request, send_file, session
from flask_sock import Sock
from google import genai

import db

SAMPLE_RATE = 16000
MIN_AUDIO_BYTES = SAMPLE_RATE
PCM_BYTES_PER_SECOND = SAMPLE_RATE * 2
MAX_STT_CHUNK_SECONDS = 15
MIN_STT_CHUNK_SECONDS = 3
MAX_STT_CHUNK_BYTES = PCM_BYTES_PER_SECOND * MAX_STT_CHUNK_SECONDS
MIN_STT_CHUNK_BYTES = PCM_BYTES_PER_SECOND * MIN_STT_CHUNK_SECONDS
LLM_MODEL = "gemini-3.1-flash-lite-preview"
SYSTEM_PROMPT = (
    "You are a helpful smart assistant for a small wearable screen. "
    "Be concise, practical, and under 80 words. "
    "If the user includes multiple intents, answer the direct question first and then briefly mention any saved todo or note action."
)
AUDIO_SUBDIR = "audio"


def load_env_file(path=".env"):
    env_path = os.path.join(os.path.dirname(__file__), path)
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            os.environ.setdefault(key, value)


load_env_file()


def parse_last_json(text):
    decoder = json.JSONDecoder()
    last = None
    pos = 0
    text = text.strip()
    while pos < len(text):
        try:
            obj, end = decoder.raw_decode(text, pos)
            last = obj
            pos = end
            while pos < len(text) and text[pos] in " \t\n\r":
                pos += 1
        except json.JSONDecodeError:
            break
    return last or {}


def ensure_audio_dir():
    db_path = os.environ.get("DATABASE_PATH", "assignment5.db")
    db_dir = os.path.dirname(os.path.abspath(db_path))
    media_root = os.path.join(db_dir, "media", AUDIO_SUBDIR)
    os.makedirs(media_root, exist_ok=True)
    return media_root


def build_audio_filename(source="device"):
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
    safe_source = re.sub(r"[^a-z0-9_-]+", "-", (source or "device").lower()).strip("-")
    safe_source = safe_source or "device"
    return f"{safe_source}-{stamp}.wav"


def save_pcm_wav(audio_bytes, source="device"):
    audio_dir = ensure_audio_dir()
    filename = build_audio_filename(source)
    full_path = os.path.join(audio_dir, filename)
    with wave.open(full_path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(audio_bytes)
    return os.path.join(AUDIO_SUBDIR, filename)


def resolve_audio_path(relative_path):
    return os.path.join(os.path.dirname(ensure_audio_dir()), relative_path)


def remove_audio_file(relative_path):
    if not relative_path:
        return
    full_path = resolve_audio_path(relative_path)
    if os.path.exists(full_path):
        os.remove(full_path)


def read_uploaded_audio(upload):
    if upload is None or not upload.filename:
        raise ValueError("audio file is required")

    payload = upload.read()
    if not payload:
        raise ValueError("audio file is empty")

    content_type = (upload.content_type or "").lower()
    filename = upload.filename.lower()

    if payload[:4] == b"RIFF" or content_type in {"audio/wav", "audio/x-wav"} or filename.endswith(".wav"):
        with wave.open(io.BytesIO(payload), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())
        if channels != 1 or sample_width != 2 or sample_rate != SAMPLE_RATE:
            raise ValueError("audio must be 16-bit mono WAV at 16000 Hz")
        return frames

    return payload


def transcribe_audio(audio_bytes):
    wit_token = os.environ.get("WIT_TOKEN")
    if not wit_token:
        return "(transcription unavailable: set WIT_TOKEN)"

    chunks = split_audio_for_stt(audio_bytes)
    print(
        f"[stt] total_bytes={len(audio_bytes)} chunk_count={len(chunks)} "
        f"chunk_seconds~={len(audio_bytes) / PCM_BYTES_PER_SECOND:.2f}"
    )
    transcripts = [transcribe_audio_chunk(wit_token, chunk) for chunk in chunks]
    merged = merge_transcript_segments(transcripts)
    print(f"[stt] merged_transcript={merged!r}")
    return merged or "(no speech detected)"


def transcribe_audio_chunk(wit_token, audio_bytes):
    req = urllib.request.Request(
        "https://api.wit.ai/speech?v=20240101",
        data=audio_bytes,
        headers={
            "Authorization": f"Bearer {wit_token}",
            "Content-Type": "audio/raw;encoding=signed-integer;bits=16;rate=16000;endian=little",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
    result = parse_last_json(body)
    transcript = result.get("text", "").strip()
    print(
        f"[stt] chunk_bytes={len(audio_bytes)} "
        f"chunk_seconds~={len(audio_bytes) / PCM_BYTES_PER_SECOND:.2f} "
        f"transcript={transcript!r}"
    )
    return transcript


def split_audio_for_stt(audio_bytes):
    if len(audio_bytes) <= MAX_STT_CHUNK_BYTES:
        return [audio_bytes] if audio_bytes else []

    chunks = [
        audio_bytes[i : i + MAX_STT_CHUNK_BYTES]
        for i in range(0, len(audio_bytes), MAX_STT_CHUNK_BYTES)
        if audio_bytes[i : i + MAX_STT_CHUNK_BYTES]
    ]

    if len(chunks) >= 2 and len(chunks[-1]) < MIN_STT_CHUNK_BYTES:
        chunks[-2] += chunks[-1]
        chunks.pop()

    return chunks


def merge_transcript_segments(segments):
    merged = ""
    for raw_segment in segments:
        segment = " ".join((raw_segment or "").strip().split())
        if not segment or segment == "(no speech detected)":
            continue
        if not merged:
            merged = segment
            continue

        merged_words = merged.split()
        segment_words = segment.split()
        overlap = 0
        max_overlap = min(len(merged_words), len(segment_words), 6)
        for size in range(max_overlap, 0, -1):
            if [word.lower() for word in merged_words[-size:]] == [word.lower() for word in segment_words[:size]]:
                overlap = size
                break

        if overlap:
            merged = " ".join(merged_words + segment_words[overlap:])
        else:
            merged = f"{merged} {segment}"

    return merged.strip()


def local_summary(text):
    clean = " ".join(text.split())
    if len(clean) <= 80:
        return clean
    return clean[:77].rstrip() + "..."


def get_llm_client():
    api_key = os.environ.get("VERTEX_API_KEY")
    if not api_key:
        return None
    return genai.Client(vertexai=True, api_key=api_key)


def extract_json_object(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def analyze_voice_note(transcript):
    clean = " ".join(transcript.split())
    fallback = {
        "summary": local_summary(clean),
        "todo_title": None,
        "todo_titles": [],
    }

    if len(clean.split()) < 4:
        return fallback

    client = get_llm_client()
    if client is None:
        return fallback

    prompt = (
        "Analyze this voice note. Return strict JSON with exactly two keys: "
        '"summary" and "todo_titles". '
        '"summary" must be a concise dashboard-ready summary under 90 characters. '
        '"todo_titles" must be an array of zero or more short actionable todo titles under 70 characters each. '
        "Extract every distinct actionable task the user says, even if phrased naturally instead of using the word todo. "
        "Be exhaustive: if the user lists three or four tasks, include all of them. "
        "Split combined spoken lists into separate task titles. "
        "Do not return meta phrases like 'things to do today' or time words by themselves. "
        "Do not create todos for pure questions, brainstorming, general information, requests for explanation, or test prompts.\n\n"
        f"Voice note:\n{clean}"
    )

    try:
        response = client.models.generate_content(
            model=LLM_MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                system_instruction=(
                    "You extract concise summaries and all distinct actionable tasks from notes. "
                    "When the user mentions multiple tasks, return one short task title per task. "
                    "Do not omit later items from a list. "
                    "Always return valid JSON only."
                ),
            ),
        )
    except Exception:
        return fallback

    parsed = extract_json_object((response.text or "").strip())
    summary = (parsed.get("summary") or "").strip()
    raw_todo_titles = parsed.get("todo_titles")
    todo_titles = []
    if isinstance(raw_todo_titles, list):
        for item in raw_todo_titles:
            if not isinstance(item, str):
                continue
            title = item.strip()
            if title and title.lower() not in {existing.lower() for existing in todo_titles}:
                todo_titles.append(title)

    # Backward-compatible parsing if the model still returns the older key.
    legacy_todo_title = parsed.get("todo_title")
    if isinstance(legacy_todo_title, str):
        title = legacy_todo_title.strip()
        if title and title.lower() not in {existing.lower() for existing in todo_titles}:
            todo_titles.append(title)

    return {
        "summary": summary or fallback["summary"],
        "todo_title": todo_titles[0] if todo_titles else None,
        "todo_titles": todo_titles,
    }


TODO_INTENT_MARKERS = (
    "todo ",
    "todo:",
    "to do ",
    "to do:",
    "to-do ",
    "to-do:",
    "add todo ",
    "add a todo ",
    "add to do ",
    "add a to do ",
    "remember to ",
    "remind me to ",
    "i need to ",
    "i should ",
    "i have to ",
    "i must ",
    "i ought to ",
    "i'm going to ",
    "im going to ",
    "i gotta ",
    "i've got to ",
    "ive got to ",
    "i want to ",
    "i plan to ",
    "i need to remember to ",
    "i need to make sure to ",
    "make sure to ",
    "be sure to ",
    "don't let me forget to ",
    "do not let me forget to ",
    "don't forget to ",
    "do not forget to ",
    "need to ",
    "have to ",
    "must ",
    "please remember to ",
    "please remind me to ",
)


def find_explicit_todo_marker(transcript):
    clean = " ".join((transcript or "").strip().split())
    lowered = clean.lower()

    best_start = None
    best_marker = None
    for marker in TODO_INTENT_MARKERS:
        idx = lowered.find(marker)
        if idx == -1:
            continue
        if idx > 0 and lowered[idx - 1].isalnum():
            continue
        if best_start is None or idx < best_start:
            best_start = idx
            best_marker = marker

    return clean, best_start, best_marker


def explicit_todo_markers():
    return list(TODO_INTENT_MARKERS)


def question_clause_cut_markers():
    return [
        "?",
        ".",
        " and what ",
        " and when ",
        " and where ",
        " and why ",
        " and who ",
        " and how ",
        " and can you ",
        " and could you ",
        " and would you ",
        " and will you ",
        " and do ",
        " and does ",
        " and did ",
        " and is ",
        " and are ",
        " and tell me ",
        " and show me ",
        " and explain ",
        " and give me ",
        " but what ",
        " but when ",
        " but where ",
        " but why ",
        " but who ",
        " but how ",
        " also what ",
        " also when ",
        " also where ",
        " also why ",
        " also who ",
        " also how ",
    ]


TODO_ACTION_STARTS = (
    "call ",
    "email ",
    "text ",
    "phone ",
    "ring ",
    "submit ",
    "finish ",
    "start ",
    "stop ",
    "study ",
    "review ",
    "check ",
    "look up ",
    "look into ",
    "research ",
    "investigate ",
    "buy ",
    "bring ",
    "pay ",
    "schedule ",
    "send ",
    "write ",
    "clean ",
    "make ",
    "build ",
    "create ",
    "pick up ",
    "drop by ",
    "determine ",
    "update ",
    "fix ",
    "repair ",
    "replace ",
    "install ",
    "remove ",
    "print ",
    "read ",
    "prepare ",
    "go ",
    "visit ",
    "meet ",
    "attend ",
    "join ",
    "book ",
    "plan ",
    "organize ",
    "reply ",
    "respond ",
    "message ",
    "contact ",
    "order ",
    "cancel ",
    "return ",
    "renew ",
    "finish up ",
    "set up ",
    "follow up ",
    "turn in ",
    "drop off ",
    "fill out ",
    "complete ",
    "practice ",
    "wash ",
    "cook ",
    "do ",
    "take ",
    "get ",
    "grab ",
    "pack ",
    "unpack ",
    "move ",
    "file ",
    "sign ",
    "sign up ",
    "sign off ",
    "upload ",
    "download ",
    "backup ",
    "back up ",
    "charge ",
    "water ",
    "feed ",
    "fold ",
    "mow ",
    "vacuum ",
    "sweep ",
    "call back ",
    "text back ",
)

TODO_LIST_FILLERS = (
    "and later ",
    "later ",
    "and then ",
    "then ",
    "and also ",
    "also ",
    "plus ",
    "next ",
    "after that ",
    "afterwards ",
)


def starts_with_todo_action(text):
    lowered = (text or "").lower()
    return lowered.startswith(tuple(explicit_todo_markers())) or lowered.startswith(TODO_ACTION_STARTS)


def normalize_todo_fragment(text):
    cleaned = " ".join((text or "").strip().split()).strip(" .:,;!?")
    lowered = cleaned.lower()

    changed = True
    while changed and cleaned:
        changed = False
        for filler in TODO_LIST_FILLERS:
            if lowered.startswith(filler):
                cleaned = cleaned[len(filler):].strip(" .:,;!?")
                lowered = cleaned.lower()
                changed = True
                break

    if lowered.startswith("and "):
        remainder = cleaned[4:].strip(" .:,;!?")
        if remainder and starts_with_todo_action(remainder):
            cleaned = remainder

    trailing_fillers = (
        " later",
        " then",
        " also",
        " next",
        " afterwards",
        " after that",
    )
    lowered = cleaned.lower()
    changed = True
    while changed and cleaned:
        changed = False
        for filler in trailing_fillers:
            if lowered.endswith(filler):
                cleaned = cleaned[: -len(filler)].strip(" .:,;!?")
                lowered = cleaned.lower()
                changed = True
                break

    return cleaned


def is_likely_todo_action(text):
    cleaned = normalize_todo_fragment(text)
    lowered = cleaned.lower()
    if not lowered:
        return False

    return starts_with_todo_action(lowered)


def split_todo_fragment(text):
    piece = normalize_todo_fragment(text)
    if not piece:
        return []

    candidate_markers = [
        " then later ",
        " and later ",
        " and then ",
        ", then ",
        " then ",
        " and also ",
        ", also ",
        " also ",
        "; ",
        ", and ",
        ", ",
        " and ",
    ]

    lowered_piece = piece.lower()
    for marker in candidate_markers:
        search_start = 0
        while True:
            idx = lowered_piece.find(marker, search_start)
            if idx == -1:
                break

            left = piece[:idx]
            right = piece[idx + len(marker):]
            if is_likely_todo_action(left) and is_likely_todo_action(right):
                return split_todo_fragment(left) + split_todo_fragment(right)

            search_start = idx + 1

    return [piece]


def split_todo_clause(title):
    clean = " ".join((title or "").strip().split())
    if not clean:
        return []

    cleaned = []
    seen = set()
    for item in split_todo_fragment(clean):
        normalized = item.strip(" .:,;!?")
        lowered = normalized.lower().rstrip(" ,")
        if lowered.endswith(" and"):
            normalized = normalized[:-4].rstrip(" .:,;!?")
            lowered = normalized.lower()
        if normalized and lowered not in seen:
            seen.add(lowered)
            cleaned.append(normalized)

    return cleaned


def split_llm_todo_title(title):
    clean = " ".join((title or "").strip().split())
    if not clean:
        return []

    lowered = clean.lower()
    positions = {0}
    for marker in TODO_ACTION_STARTS:
        search_start = 0
        while True:
            idx = lowered.find(marker, search_start)
            if idx == -1:
                break
            if idx == 0 or lowered[idx - 1] == " ":
                positions.add(idx)
            search_start = idx + 1

    ordered_positions = sorted(positions)
    if ordered_positions == [0]:
        return split_todo_clause(clean)

    split_titles = []
    for i, start in enumerate(ordered_positions):
        end = ordered_positions[i + 1] if i + 1 < len(ordered_positions) else len(clean)
        segment = clean[start:end].strip(" .:,;!?")
        if not segment:
            continue
        for item in split_todo_clause(segment):
            if len(todo_title_keywords(item)) >= 2 or len(item.split()) >= 2:
                split_titles.append(item)

    return merge_todo_titles(split_titles)


def is_valid_explicit_todo_title(title):
    normalized = " ".join((title or "").strip().split()).strip(" .:,;!?")
    lowered = normalized.lower()
    if not lowered:
        return False

    invalid_titles = {
        "today",
        "tomorrow",
        "later",
        "right now",
        "soon",
        "this week",
        "this weekend",
        "tonight",
        "multiple things",
        "a few things",
        "some things",
        "things to do",
    }
    if lowered in invalid_titles:
        return False

    if lowered.startswith("do today"):
        return False
    if lowered.startswith("today the first is to"):
        return False
    if lowered.startswith("multiple things i need to"):
        return False
    if lowered.startswith("things i need to"):
        return False
    if re.search(r"\b(first|second|third|fourth|fifth|\d+(?:st|nd|rd|th))\s+is\s+to\b", lowered):
        return False

    return True


def extract_enumerated_todo_titles(transcript):
    clean = " ".join((transcript or "").strip().split())
    if not clean:
        return []

    pattern = re.compile(
        r"(?:^|.*?\b)(?:the\s+)?(?:first|second|third|fourth|fifth|next|last|\d+(?:st|nd|rd|th))\s+is\s+to\s+(.+?)(?=(?:\s+(?:the\s+)?(?:first|second|third|fourth|fifth|next|last|\d+(?:st|nd|rd|th))\s+is\s+to\b)|[.?!]?$)",
        re.IGNORECASE,
    )

    titles = []
    seen = set()
    for match in pattern.finditer(clean):
        for title in split_todo_clause(match.group(1)):
            lowered = title.lower()
            if is_valid_explicit_todo_title(title) and lowered not in seen:
                seen.add(lowered)
                titles.append(title)
    return titles


def extract_explicit_todo_titles(transcript):
    clean = " ".join((transcript or "").strip().split())
    lowered = clean.lower()
    markers = explicit_todo_markers()
    cut_markers = question_clause_cut_markers()

    occurrences = []
    for marker in markers:
        start = 0
        while True:
            idx = lowered.find(marker, start)
            if idx == -1:
                break
            if idx == 0 or not lowered[idx - 1].isalnum():
                occurrences.append((idx, marker))
            start = idx + 1

    occurrences.sort(key=lambda item: item[0])
    titles = []

    for i, (idx, marker) in enumerate(occurrences):
        next_start = occurrences[i + 1][0] if i + 1 < len(occurrences) else len(clean)
        title = clean[idx + len(marker):next_start]
        lowered_title = title.lower()

        cut_at = None
        for cut_marker in cut_markers:
            cut_idx = lowered_title.find(cut_marker)
            if cut_idx == -1:
                continue
            if cut_at is None or cut_idx < cut_at:
                cut_at = cut_idx

        if cut_at is not None:
            title = title[:cut_at]

        for split_title in split_todo_clause(title):
            if (
                is_valid_explicit_todo_title(split_title)
                and split_title.lower() not in {item["title"].lower() for item in titles}
            ):
                titles.append({"title": split_title})

    for title in extract_enumerated_todo_titles(clean):
        if title.lower() not in {item["title"].lower() for item in titles}:
            titles.append({"title": title})

    return [item["title"] for item in titles]


def extract_explicit_todo_title(transcript):
    titles = extract_explicit_todo_titles(transcript)
    return titles[0] if titles else None


def extract_question_clause(transcript):
    clean, todo_start, _marker = find_explicit_todo_marker(transcript)
    if not transcript_has_question_intent(clean):
        return None

    question = clean
    if todo_start is not None and todo_start > 0:
        question = clean[:todo_start]

    cleanup_suffixes = [
        " and also later",
        " and also",
        " and then",
        " and",
        " but also",
        " but",
        " also",
        ",",
    ]
    question = question.rstrip(" .:,;!?")
    lowered = question.lower()
    changed = True
    while changed and question:
        changed = False
        for suffix in cleanup_suffixes:
            if lowered.endswith(suffix):
                question = question[: -len(suffix)].rstrip(" .:,;!?")
                lowered = question.lower()
                changed = True
                break

    return question or None


def maybe_create_todo(transcript):
    title = extract_explicit_todo_title(transcript)
    if title:
        return db.insert_todo(title)
    return None


def maybe_create_todos(transcript):
    titles = extract_explicit_todo_titles(transcript)
    return [db.insert_todo(title) for title in titles]


def todo_title_keywords(title):
    stopwords = {
        "a",
        "an",
        "the",
        "my",
        "your",
        "our",
        "their",
        "that",
        "this",
        "these",
        "those",
        "some",
        "later",
    }
    normalized = " ".join((title or "").strip().split()).strip(" .:,;!?").lower()
    return {word for word in re.findall(r"[a-z0-9']+", normalized) if len(word) > 1 and word not in stopwords}


def are_similar_todo_titles(left, right):
    left_normalized = " ".join((left or "").strip().split()).strip(" .:,;!?").lower()
    right_normalized = " ".join((right or "").strip().split()).strip(" .:,;!?").lower()
    if not left_normalized or not right_normalized:
        return False
    if left_normalized == right_normalized:
        return True

    left_keywords = todo_title_keywords(left)
    right_keywords = todo_title_keywords(right)
    if not left_keywords or not right_keywords:
        return False

    return left_keywords <= right_keywords or right_keywords <= left_keywords


def merge_todo_titles(*groups):
    merged = []
    for group in groups:
        for title in group or []:
            normalized = " ".join((title or "").strip().split()).strip(" .:,;!?")
            if not normalized:
                continue

            replacement_index = None
            for idx, existing in enumerate(merged):
                if are_similar_todo_titles(existing, normalized):
                    replacement_index = idx
                    break

            if replacement_index is None:
                merged.append(normalized)
            elif len(normalized) > len(merged[replacement_index]):
                merged[replacement_index] = normalized
    return merged


def should_ignore_extracted_todo(transcript):
    lowered = " ".join(transcript.lower().strip().split())
    ignored_prefixes = [
        "can you ",
        "could you ",
        "would you ",
        "will you ",
        "what ",
        "when ",
        "where ",
        "why ",
        "who ",
        "how ",
        "is ",
        "are ",
        "do ",
        "does ",
        "did ",
        "give me ",
        "tell me ",
        "show me ",
        "explain ",
        "list ",
        "generate ",
        "write ",
    ]
    ignored_phrases = [
        "so i can test",
        "for testing",
        "to test",
        "test the assistant",
        "test this",
        "example text",
        "dummy text",
        "sample text",
        "long list of text",
    ]
    explicit_todo_signals = [
        "todo ",
        "todo:",
        "to do ",
        "to do:",
        "to-do ",
        "to-do:",
        "remember to ",
        "remind me to ",
        "i need to ",
        "i should ",
        "i have to ",
        "don't let me forget to ",
        "do not let me forget to ",
    ]
    if lowered.startswith(tuple(explicit_todo_signals)):
        return False
    if transcript.strip().endswith("?"):
        return True
    return lowered.startswith(tuple(ignored_prefixes)) or any(
        phrase in lowered for phrase in ignored_phrases
    )


def should_accept_extracted_todo(transcript, todo_title):
    if not todo_title:
        return False

    lowered = " ".join(transcript.lower().strip().split())
    explicit_todo_starts = [
        "todo ",
        "todo:",
        "to do ",
        "to do:",
        "to-do ",
        "to-do:",
        "add todo ",
        "add a todo ",
        "add to do ",
        "add a to do ",
        "remember to ",
        "remind me to ",
        "i need to ",
        "i should ",
        "i have to ",
        "don't let me forget to ",
        "do not let me forget to ",
    ]

    if lowered.startswith(tuple(explicit_todo_starts)):
        return True

    explicit_todo_markers = [
        " remember to ",
        " remind me to ",
        " i need to ",
        " i should ",
        " i have to ",
        " i must ",
        " don't let me forget to ",
        " do not let me forget to ",
    ]
    padded = f" {lowered} "
    if any(marker in padded for marker in explicit_todo_markers):
        return True

    if should_ignore_extracted_todo(transcript):
        return False

    return False


def should_accept_llm_todo_title(transcript, todo_title):
    normalized = " ".join((todo_title or "").strip().split()).strip(" .:,;!?")
    lowered = normalized.lower()
    transcript_lowered = " ".join((transcript or "").strip().lower().split())
    if not normalized:
        return False
    if not is_valid_explicit_todo_title(normalized):
        return False

    rejected_titles = {
        "task",
        "tasks",
        "to do",
        "todo",
        "to-do",
        "reminder",
        "reminders",
        "plan",
        "plans",
        "today's tasks",
        "things to do today",
    }
    if lowered in rejected_titles:
        return False
    if len(lowered) < 4:
        return False

    # Keep question-only utterances from turning into invented tasks.
    if should_ignore_extracted_todo(transcript) and not should_accept_extracted_todo(transcript, normalized):
        return False

    # Require some lexical overlap so the model cannot invent unrelated work.
    title_words = {word for word in re.findall(r"[a-z0-9']+", lowered) if len(word) > 2}
    transcript_words = {word for word in re.findall(r"[a-z0-9']+", transcript_lowered) if len(word) > 2}
    if title_words and transcript_words and not (title_words & transcript_words):
        return False

    return True


def accepted_extracted_todo_titles(transcript, todo_titles):
    accepted = []
    for title in todo_titles or []:
        if should_accept_llm_todo_title(transcript, title):
            split_titles = split_llm_todo_title(title)
            if len(split_titles) > 1:
                accepted.extend(split_titles)
            else:
                accepted.append(title)
    return merge_todo_titles(accepted)


def build_todo_titles_from_note(transcript, note_analysis):
    llm_todo_titles = accepted_extracted_todo_titles(
        transcript,
        note_analysis.get("todo_titles") or [note_analysis.get("todo_title")],
    )
    explicit_todo_titles = extract_explicit_todo_titles(transcript)
    merged_todo_titles = merge_todo_titles(llm_todo_titles, explicit_todo_titles)
    if merged_todo_titles:
        return merged_todo_titles

    # Fallback only when the LLM is unavailable or returns nothing useful.
    return []


def build_fallback_response(transcript, created_todos):
    if created_todos:
        if len(created_todos) == 1:
            return f"Added to your to-do list: {created_todos[0]['title']}"
        preview = ", ".join(item["title"] for item in created_todos[:3])
        return f"Added {len(created_todos)} to-dos: {preview}"

    open_todos = db.fetch_todos(limit=3, include_completed=False)
    if open_todos:
        preview = ", ".join(item["title"] for item in open_todos)
        return f"Saved your note. Current to-dos: {preview}"

    return "Saved your note."


def transcript_has_question_intent(transcript):
    clean = " ".join((transcript or "").strip().lower().split())
    if not clean:
        return False
    if "?" in clean:
        return True
    question_starts = [
        "what ",
        "when ",
        "where ",
        "why ",
        "who ",
        "how ",
        "can you ",
        "could you ",
        "would you ",
        "will you ",
        "is ",
        "are ",
        "do ",
        "does ",
        "did ",
        "tell me ",
        "show me ",
        "explain ",
    ]
    return clean.startswith(tuple(question_starts))


def ensure_todo_acknowledged(response_text, created_todos):
    if not created_todos:
        return response_text

    clean = (response_text or "").strip()
    if len(created_todos) == 1:
        title = created_todos[0]["title"]
        lowered = clean.lower()
        if title.lower() in lowered or "to-do" in lowered or "todo" in lowered:
            return clean

        suffix = f" I also added this to your to-do list: {title}."
        return (clean + suffix).strip() if clean else f"Added to your to-do list: {title}"

    preview = ", ".join(item["title"] for item in created_todos[:3])
    lowered = clean.lower()
    if "to-do" in lowered or "todo" in lowered:
        return clean

    suffix = f" I also added {len(created_todos)} to-dos: {preview}."
    return (clean + suffix).strip() if clean else f"Added {len(created_todos)} to-dos: {preview}"


def process_audio_note(audio_bytes, source="device"):
    audio_path = save_pcm_wav(audio_bytes, source=source)

    try:
        transcript = transcribe_audio(audio_bytes)
    except Exception as exc:
        transcript = f"(transcription error: {exc})"

    note_analysis = analyze_voice_note(transcript)
    summary = note_analysis["summary"]
    todo_titles = build_todo_titles_from_note(transcript, note_analysis)
    created_todos = [db.insert_todo(title) for title in todo_titles]

    note = db.insert_note(
        transcript=transcript,
        summary=summary,
        audio_path=audio_path,
        source=source,
    )

    return {
        "audio_path": audio_path,
        "created_todo": created_todos[0] if created_todos else None,
        "created_todos": created_todos,
        "note": note,
        "summary": summary,
        "transcript": transcript,
    }


def generate_assistant_response(transcript, created_todos):
    client = get_llm_client()
    if client is None:
        return build_fallback_response(transcript, created_todos)

    question_clause = extract_question_clause(transcript)
    prompt = (
        f"User said: {transcript}\n"
        "Respond directly to the user's request."
    )
    if created_todos:
        todo_summary = "; ".join(item["title"] for item in created_todos[:3])
        prompt = (
            f"User said: {question_clause or transcript}\n"
            f"These to-dos were already created: {todo_summary}.\n"
            "If the user asked a question or made a request, answer that first in one short sentence. "
            "Then mention in a second short sentence that the to-do item or items were saved. "
            "Do not skip either part."
        )
    elif question_clause:
        prompt = (
            f"User said: {question_clause}\n"
            "Answer the user's direct question or request briefly and clearly."
        )

    response = client.models.generate_content(
        model=LLM_MODEL,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        ),
    )
    text = (response.text or "").strip()
    if not text:
        return build_fallback_response(transcript, created_todos)
    return ensure_todo_acknowledged(text, created_todos)


def dashboard_unauthorized():
    return Response(
        "Dashboard authentication required.",
        401,
        {"WWW-Authenticate": 'Basic realm="Assignment 5 Dashboard"'},
    )


def dashboard_auth_is_valid():
    if session.get("dashboard_authenticated") is True:
        return True

    auth = request.authorization
    expected_user = os.environ.get("DASHBOARD_USERNAME", "admin")
    expected_password = os.environ.get("DASHBOARD_PASSWORD", "replace_me")

    if auth is None or auth.username is None or auth.password is None:
        return False

    return compare_digest(auth.username, expected_user) and compare_digest(
        auth.password, expected_password
    )


def require_dashboard_auth(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not dashboard_auth_is_valid():
            return dashboard_unauthorized()
        return view_func(*args, **kwargs)

    return wrapped


def device_api_key_is_valid():
    expected_key = os.environ.get("DEVICE_API_KEY", "replace_me")
    provided_key = request.headers.get("X-Device-API-Key", "") or request.args.get(
        "api_key", ""
    )
    return compare_digest(provided_key, expected_key)


def device_unauthorized():
    return jsonify({"status": "error", "message": "invalid device api key"}), 401


def device_or_dashboard_auth_is_valid():
    return device_api_key_is_valid() or dashboard_auth_is_valid()


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("APP_SECRET_KEY", "dev-secret")
    app.config["DATABASE_PATH"] = os.environ.get("DATABASE_PATH", "assignment5.db")

    db.init_app(app)
    sock = Sock(app)

    @app.get("/healthz")
    def healthcheck():
        return jsonify({"status": "ok", "database_path": app.config["DATABASE_PATH"]})

    @app.get("/")
    @require_dashboard_auth
    def dashboard():
        session["dashboard_authenticated"] = True
        return render_template("dashboard.html")

    @app.get("/api/todos")
    @require_dashboard_auth
    def list_todos():
        return jsonify(
            {
                "items": db.fetch_todos(),
                "status": "ok",
            }
        )

    @app.post("/api/todos")
    @require_dashboard_auth
    def create_todo():
        payload = request.get_json(silent=True) or {}
        title = (payload.get("title") or "").strip()
        if not title:
            return jsonify({"status": "error", "message": "title is required"}), 400

        item = db.insert_todo(title)
        return jsonify({"status": "created", "item": item}), 201

    @app.post("/api/todos/<int:todo_id>/complete")
    @require_dashboard_auth
    def complete_todo(todo_id):
        item = db.mark_todo_complete(todo_id)
        if item is None:
            return jsonify({"status": "error", "message": "todo not found"}), 404
        return jsonify({"status": "ok", "item": item})

    @app.post("/api/todos/<int:todo_id>/edit")
    @require_dashboard_auth
    def edit_todo(todo_id):
        payload = request.get_json(silent=True) or {}
        title = (payload.get("title") or "").strip()
        if not title:
            return jsonify({"status": "error", "message": "title is required"}), 400

        item = db.update_todo_title(todo_id, title)
        if item is None:
            return jsonify({"status": "error", "message": "todo not found"}), 404
        return jsonify({"status": "ok", "item": item})

    @app.post("/api/todos/<int:todo_id>/delete")
    @require_dashboard_auth
    def remove_todo(todo_id):
        item = db.delete_todo(todo_id)
        if item is None:
            return jsonify({"status": "error", "message": "todo not found"}), 404
        return jsonify({"status": "deleted", "item": item})

    @app.post("/api/todos/clear")
    @require_dashboard_auth
    def clear_todos():
        db.clear_todos()
        return jsonify({"status": "cleared"})

    @app.get("/api/notes")
    @require_dashboard_auth
    def list_notes():
        return jsonify(
            {
                "items": db.fetch_notes(),
                "status": "ok",
            }
        )

    @app.post("/api/notes")
    @require_dashboard_auth
    def create_note():
        payload = request.get_json(silent=True) or {}
        transcript = (payload.get("transcript") or "").strip()
        if not transcript:
            return jsonify({"status": "error", "message": "transcript is required"}), 400

        note_analysis = analyze_voice_note(transcript)
        summary = (payload.get("summary") or "").strip() or note_analysis["summary"]
        todo_titles = build_todo_titles_from_note(transcript, note_analysis)
        created_todos = [db.insert_todo(title) for title in todo_titles]

        item = db.insert_note(
            transcript=transcript,
            summary=summary,
            audio_path=(payload.get("audio_path") or "").strip() or None,
            source=(payload.get("source") or "device").strip() or "device",
        )
        return (
            jsonify(
                {
                    "status": "created",
                    "item": item,
                    "created_todo": created_todos[0] if created_todos else None,
                    "created_todos": created_todos,
                }
            ),
            201,
        )

    @app.post("/api/audio")
    @require_dashboard_auth
    def upload_audio():
        try:
            audio_bytes = read_uploaded_audio(request.files.get("audio"))
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

        result = process_audio_note(audio_bytes, source="dashboard-upload")
        return (
            jsonify(
                {
                    "status": "created",
                    "item": result["note"],
                    "created_todo": result["created_todo"],
                    "created_todos": result["created_todos"],
                }
            ),
            201,
        )

    @app.get("/api/notes/<int:note_id>/audio")
    @require_dashboard_auth
    def get_note_audio(note_id):
        note = db.fetch_note(note_id)
        if note is None:
            return jsonify({"status": "error", "message": "note not found"}), 404
        audio_path = note.get("audio_path")
        if not audio_path:
            return jsonify({"status": "error", "message": "note has no audio"}), 404

        full_path = resolve_audio_path(audio_path)
        if not os.path.exists(full_path):
            return jsonify({"status": "error", "message": "audio file missing"}), 404
        return send_file(full_path, mimetype="audio/wav", conditional=True)

    @app.post("/api/notes/<int:note_id>/delete")
    @require_dashboard_auth
    def delete_note(note_id):
        note = db.delete_note(note_id)
        if note is None:
            return jsonify({"status": "error", "message": "note not found"}), 404
        remove_audio_file(note.get("audio_path"))
        return jsonify({"status": "deleted", "item": note})

    @app.post("/api/notes/clear")
    @require_dashboard_auth
    def clear_notes():
        try:
            for note in db.fetch_notes():
                remove_audio_file(note.get("audio_path"))
            db.clear_notes()
        except Exception as exc:
            return jsonify({"status": "error", "message": f"failed to clear notes: {exc}"}), 500
        return jsonify({"status": "ok"})

    @app.get("/api/interactions")
    @require_dashboard_auth
    def list_interactions():
        try:
            items = db.fetch_interactions()
        except Exception as exc:
            return jsonify(
                {
                    "items": [],
                    "message": f"interactions temporarily unavailable: {exc}",
                    "status": "degraded",
                }
            )
        return jsonify({"items": items, "status": "ok"})

    @app.post("/api/interactions/<int:interaction_id>/delete")
    @require_dashboard_auth
    def delete_interaction(interaction_id):
        item = db.delete_interaction(interaction_id)
        if item is None:
            return jsonify({"status": "error", "message": "interaction not found"}), 404
        return jsonify({"status": "deleted", "item": item})

    @app.post("/api/interactions/clear")
    @require_dashboard_auth
    def clear_interactions():
        db.clear_interactions()
        return jsonify({"status": "ok"})

    @app.get("/api/device/state")
    def device_state():
        if not device_or_dashboard_auth_is_valid():
            return device_unauthorized()

        todos = db.fetch_todos(limit=5, include_completed=False)
        notes = db.fetch_notes(limit=1)
        return jsonify(
            {
                "mode": "todo",
                "todo_preview": todos,
                "last_note": notes[0] if notes else None,
                "status": "ok",
            }
        )

    @app.post("/api/device/todos/<int:todo_id>/complete")
    def complete_device_todo(todo_id):
        if not device_api_key_is_valid():
            return device_unauthorized()

        item = db.mark_todo_complete(todo_id)
        if item is None:
            return jsonify({"status": "error", "message": "todo not found"}), 404
        return jsonify({"status": "ok", "item": item})

    @sock.route("/ws/assistant")
    def assistant_socket(ws):
        if not device_api_key_is_valid():
            ws.send("R:Unauthorized device.")
            ws.send("D")
            return

        interaction = None
        audio_buffer = bytearray()
        recording = False

        while True:
            message = ws.receive()
            if message is None:
                if interaction is not None:
                    db.update_interaction(interaction["id"], status="closed")
                break

            if isinstance(message, str):
                if message == "start":
                    interaction = db.insert_interaction(status="recording")
                    audio_buffer.clear()
                    recording = True

                elif message == "cancel":
                    recording = False
                    audio_buffer.clear()
                    if interaction is not None:
                        db.delete_interaction(interaction["id"])
                    ws.send("R:Recording canceled.")
                    ws.send("D")
                    interaction = None

                elif message == "stop":
                    recording = False
                    if len(audio_buffer) < MIN_AUDIO_BYTES:
                        if interaction is not None:
                            db.update_interaction(interaction["id"], status="too_short")
                        ws.send("T:(too short)")
                        ws.send("R:Hold the button a little longer and try again.")
                        ws.send("D")
                        interaction = None
                        continue

                    try:
                        processed = process_audio_note(bytes(audio_buffer), source="device")
                        transcript = processed["transcript"]
                        created_todos = processed["created_todos"]
                    except Exception as exc:
                        transcript = f"(transcription error: {exc})"
                        created_todos = []

                    ws.send(f"T:{transcript}")

                    try:
                        assistant_response = generate_assistant_response(transcript, created_todos)
                        status = "completed"
                    except Exception as exc:
                        assistant_response = f"Error generating response: {exc}"
                        status = "error"

                    if interaction is not None:
                        db.update_interaction(
                            interaction["id"],
                            transcript=transcript,
                            assistant_response=assistant_response,
                            status=status,
                        )

                    ws.send(f"R:{assistant_response}")
                    ws.send("D")
                    interaction = None

            elif isinstance(message, bytes) and recording:
                audio_buffer.extend(message)

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host="0.0.0.0", port=port, debug=debug)
