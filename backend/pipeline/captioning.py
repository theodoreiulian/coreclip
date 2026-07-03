import os
import json
import logging
from sqlalchemy.orm import Session
from models.clip import Clip
from services import ffmpeg_client
from config import config

logger = logging.getLogger("clipper")

def _generate_ass(words: list, y_position: float, frame_width: int, frame_height: int) -> str:
    ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {frame_width}
PlayResY: {frame_height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Montserrat,48,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,0,2,20,20,{int(frame_height - y_position)},1
Style: Highlight,Montserrat,48,&H0000FFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,0,2,20,20,{int(frame_height - y_position)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    def format_time(t):
        h = int(t / 3600)
        m = int((t % 3600) / 60)
        s = int(t % 60)
        cs = int((t % 1) * 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
        
    for chunk in words:
        if not chunk:
            continue
            
        phrase_start = chunk[0]['start']
        phrase_end = chunk[-1]['end']
        
        text_parts = []
        for w in chunk:
            rel_start = int((w['start'] - phrase_start) * 1000)
            rel_end = int((w['end'] - phrase_start) * 1000)
            
            # Use \t to animate the primary color (\c) to simulate \rHighlight
            # \c&HFFFFFF& is white (Default), \c&H00FFFF& is yellow (Highlight)
            part = f"{{\\c&HFFFFFF&\\t({rel_start},{rel_start},\\c&H00FFFF&)\\t({rel_end},{rel_end},\\c&HFFFFFF&)}}{w['word']}"
            text_parts.append(part)
            
        line_text = " ".join(text_parts)
        ass += f"Dialogue: 0,{format_time(phrase_start)},{format_time(phrase_end)},Default,,0,0,0,,{line_text}\n"

    return ass

def generate(clip: Clip, db: Session):
    logger.info(f"Generating captions for clip {clip.clip_index}")

    if clip.vertical_clip_path and os.path.exists(clip.vertical_clip_path):
        input_vid = os.path.abspath(clip.vertical_clip_path)
    else:
        input_vid = os.path.abspath(clip.raw_clip_path)
    y_position = 1920 * 0.65
        
    clip_dir = os.path.dirname(clip.raw_clip_path)
    transcript_path = os.path.join(clip_dir, "transcript.json")
    
    with open(transcript_path, 'r', encoding='utf-8') as f:
        transcript_data = json.load(f)
        
    all_words = []
    for seg in transcript_data.get('segments', []):
        all_words.extend(seg.get('words', []))
    
    # Filter out zero-duration words by merging with next word
    filtered_words = []
    i = 0
    while i < len(all_words):
        w = all_words[i]
        if w['end'] - w['start'] <= 0 and i + 1 < len(all_words):
            # Merge zero-duration word with the next word
            next_w = all_words[i + 1]
            filtered_words.append({
                'word': w['word'] + ' ' + next_w['word'],
                'start': next_w['start'],
                'end': next_w['end']
            })
            i += 2
        else:
            filtered_words.append(w)
            i += 1
        
    chunks = []
    current_chunk = []
    
    for i, word in enumerate(filtered_words):
        word_dur = word['end'] - word['start']
        
        # Very long words (>1s) get displayed alone
        if word_dur > 1.0:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
            chunks.append([word])
            continue
            
        if not current_chunk:
            current_chunk.append(word)
            continue
            
        prev_word = current_chunk[-1]
        gap = word['start'] - prev_word['end']
        
        # Insert blank event for gaps > 500ms
        if gap > 0.5:
            chunks.append(current_chunk)
            current_chunk = [word]
        elif len(current_chunk) >= 3 or gap > 0.3:
            chunks.append(current_chunk)
            current_chunk = [word]
        else:
            current_chunk.append(word)
            
    if current_chunk:
        chunks.append(current_chunk)
        
    ass_content = _generate_ass(chunks, y_position, 1080, 1920)
    
    ass_path = os.path.abspath(os.path.join(clip_dir, "captions.ass"))
    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write(ass_content)

    fonts_dir = os.path.abspath(config.FONT_DIR)
    os.makedirs(fonts_dir, exist_ok=True)

    captioned_path = os.path.abspath(os.path.join(clip_dir, "captioned.mp4"))

    args = ffmpeg_client.build_final_burn_args(
        input_path=input_vid,
        ass_path=ass_path,
        output_path=captioned_path,
        fonts_dir=fonts_dir
    )
    
    ffmpeg_client.run_ffmpeg(args, f"Burn captions clip {clip.clip_index}")
    
    clip.final_clip_path = captioned_path
    db.commit()
    logger.info(f"Captions generated for clip {clip.clip_index}")
