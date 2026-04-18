import os
import json
import asyncio
import random
import re
import time
from PIL import Image

# --- KRİTİK HATA DÜZELTİCİ (Monkey Patch) ---
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.LANCZOS

from moviepy.editor import (
    ImageClip, AudioFileClip, concatenate_videoclips, 
    CompositeAudioClip, TextClip, CompositeVideoClip
)
import moviepy.video.fx.all as vfx
import moviepy.audio.fx.all as afx
from huggingface_hub import snapshot_download
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
import edge_tts

# --- GENEL AYARLAR ---
HF_REPO = os.getenv("HF_REPO_ID")
HF_TOKEN = os.getenv("HF_TOKEN")
VIDEO_FONT = "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf" 
FONT_COLOR = "yellow"
FONT_SIZE = 95 
VIDEO_W, VIDEO_H = 1080, 1920 

def process_image_to_fill(img_path):
    pil_img = Image.open(img_path).convert("RGB")
    img_w, img_h = pil_img.size
    screen_ratio = VIDEO_W / VIDEO_H
    img_ratio = img_w / img_h
    
    if img_ratio > screen_ratio:
        new_h = VIDEO_H
        new_w = int(VIDEO_H * img_ratio)
    else:
        new_w = VIDEO_W
        new_h = int(VIDEO_W / img_ratio)
    
    pil_img = pil_img.resize((new_w, new_h), Image.ANTIALIAS)
    left = (new_w - VIDEO_W) / 2
    top = (new_h - VIDEO_H) / 2
    pil_img = pil_img.crop((left, top, left + VIDEO_W, top + VIDEO_H))
    
    temp_path = f"temp_{random.randint(1000,9999)}.jpg"
    pil_img.save(temp_path, quality=95)
    return ImageClip(temp_path)

def apply_random_ken_burns(clip):
    if random.random() > 0.65:
        return clip

    duration = clip.duration
    zoom_factor = random.uniform(1.10, 1.20)
    base_clip = clip.resize(zoom_factor)
    
    max_x = int(base_clip.w - VIDEO_W)
    max_y = int(base_clip.h - VIDEO_H)
    start_x, start_y = random.randint(0, max_x), random.randint(0, max_y)
    end_x, end_y = random.randint(0, max_x), random.randint(0, max_y)

    def make_frame(get_frame, t):
        frame = get_frame(t)
        curr_x = int(start_x + (end_x - start_x) * (t / duration))
        curr_y = int(start_y + (end_y - start_y) * (t / duration))
        curr_x = max(0, min(curr_x, max_x))
        curr_y = max(0, min(curr_y, max_y))
        return frame[curr_y : curr_y + VIDEO_H, curr_x : curr_x + VIDEO_W]

    return base_clip.fl(make_frame)

def upload_to_youtube(video_path, meta, lang_code):
    try:
        secret_name = f"YT_TOKEN_{lang_code.upper()}"
        token_env = os.getenv(secret_name)
        if not token_env: return
        token_data = json.loads(token_env)
        creds = Credentials.from_authorized_user_info(token_data)
        youtube = build("youtube", "v3", credentials=creds)
        
        if lang_code == "es":
            yt_lang = "es-ES"
            fixed_tags = ["Crecimiento Personal", "Mentalidad", "Datos del Cerebro", "Verdades Ocultas", "Datos Psicológicos", "Psicología Social", "Por qué somos así."]
        else:
            yt_lang = "en-US"
            fixed_tags = ["Self Improvement", "Mindset", "Brain Facts", "Hidden Truths", "Psychological Facts", "Social Psychology", "Why We Do What We Do"]

        final_tags = list(set(fixed_tags + meta.get('tags', [])))

        body = {
            "snippet": {
                "title": meta['title'], "description": meta['description'], "tags": final_tags,
                "categoryId": "22", "defaultAudioLanguage": yt_lang, "defaultLanguage": yt_lang
            },
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
        }
        
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
        print(f"[{lang_code.upper()}] Yüklendi! ID: {response['id']}")
    except Exception as e:
        print(f"YT {lang_code} Hatası: {e}")

async def create_video_for_lang(lang_code, data, folder_idx, local_folder):
    lang_data = data[lang_code]
    voice_file = f"voice_{lang_code}.mp3"
    await edge_tts.Communicate(lang_data['script'], lang_data['voice']).save(voice_file)
    voice_audio = AudioFileClip(voice_file)
    
    image_files = [os.path.join(local_folder, f"{i}{ext}") for i in range(1, 13) 
                   for ext in [".png", ".jpg", ".jpeg", ".PNG", ".JPG"] 
                   if os.path.exists(os.path.join(local_folder, f"{i}{ext}"))]
    random.shuffle(image_files)
    
    clips = []
    current_time = 0.0
    img_dur = 4.5 
    while current_time < voice_audio.duration:
        img_path = image_files[len(clips) % len(image_files)]
        clip = process_image_to_fill(img_path).set_duration(img_dur).set_fps(24)
        clip = apply_random_ken_burns(clip)
        clips.append(clip)
        current_time += img_dur

    video = concatenate_videoclips(clips, method="compose").set_duration(voice_audio.duration)
    
    words = lang_data['script'].split()
    chunks = [" ".join(words[i:i+3]) for i in range(0, len(words), 3)]
    chunk_dur = voice_audio.duration / len(chunks)
    
    text_clips = []
    start_t = 0.0
    for chunk in chunks:
        txt = TextClip(
            chunk.upper(), fontsize=FONT_SIZE, font=VIDEO_FONT, color=FONT_COLOR, 
            method="caption", size=(int(VIDEO_W * 0.9), 600), align="Center",
            stroke_color="black", stroke_width=3
        ).set_duration(chunk_dur).set_start(start_t).set_position(('center', 'center'))
        text_clips.append(txt)
        start_t += chunk_dur

    bg_path = os.path.join(local_folder, "bg.mp3")
    bg = AudioFileClip(bg_path).fx(afx.audio_loop, duration=voice_audio.duration).volumex(0.15)
    
    final = CompositeVideoClip([video] + text_clips).set_audio(CompositeAudioClip([voice_audio, bg]))
    out = f"final_{lang_code}_{folder_idx}.mp4"
    final.write_videofile(out, codec="libx264", audio_codec="aac", bitrate="5000k")
    
    return out, lang_data

async def main():
    # --- RANDOM DELAY (0-8 DAKİKA) ---
    delay_seconds = random.randint(0, 480)
    print(f"Sistem uyandı, {delay_seconds // 60} dakika {delay_seconds % 60} saniye bekleniyor...")
    time.sleep(delay_seconds)

    if not os.path.exists("current_index.txt"):
        with open("current_index.txt", "w") as f: f.write("1")
    with open("current_index.txt", "r") as f:
        idx = f.read().strip()
    
    local_dir = "temp_assets"
    os.makedirs(local_dir, exist_ok=True)
    
    try:
        snapshot_download(repo_id=HF_REPO, repo_type="dataset", token=HF_TOKEN, local_dir=local_dir)
        folder_path = None
        for root, dirs, files in os.walk(local_dir):
            if root.replace(" ", "").endswith(idx) and "data.json" in files:
                folder_path = root
                break
        
        if not folder_path: raise Exception(f"{idx} klasörü bulunamadı.")
        with open(os.path.join(folder_path, "data.json"), 'r', encoding='utf-8') as f:
            all_data = json.load(f)

        v_es, m_es = await create_video_for_lang("es", all_data, idx, folder_path)
        upload_to_youtube(v_es, m_es, "es")
        
        v_en, m_en = await create_video_for_lang("en", all_data, idx, folder_path)
        upload_to_youtube(v_en, m_en, "en")

        with open("current_index.txt", "w") as f: f.write(str(int(idx) + 1))
    except Exception as e: print(f"HATA: {e}")

if __name__ == "__main__": asyncio.run(main())
