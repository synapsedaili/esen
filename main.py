import os
import json
import asyncio
import random
import re
from moviepy import ImageClip, AudioFileClip, concatenate_videoclips, CompositeAudioClip, TextClip, CompositeVideoClip
import moviepy.video.fx as vfx
import moviepy.audio.fx as afx
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
FONT_SIZE = 75 
VIDEO_W, VIDEO_H = 1080, 1920 

def process_image_to_fill(img_path):
    clip = ImageClip(img_path)
    screen_aspect_ratio = VIDEO_W / VIDEO_H
    img_aspect_ratio = clip.w / clip.h

    if img_aspect_ratio > screen_aspect_ratio:
        new_w = int(VIDEO_H * img_aspect_ratio)
        clip = clip.resized(height=VIDEO_H, width=new_w)
        clip = clip.cropped(x1=(clip.w - VIDEO_W)//2, y1=0, width=VIDEO_W, height=VIDEO_H)
    else:
        new_h = int(VIDEO_W / img_aspect_ratio)
        clip = clip.resized(width=VIDEO_W, height=new_h)
        clip = clip.cropped(x1=0, y1=(clip.h - VIDEO_H)//2, width=VIDEO_W, height=VIDEO_H)
    return clip

def apply_random_ken_burns(clip):
    duration = clip.duration
    zoom_factor = random.uniform(1.2, 1.4)
    base_clip = clip.resized(zoom_factor)
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

    return base_clip.transform(make_frame)

def upload_to_youtube(video_path, meta, lang_code):
    try:
        secret_name = f"YT_TOKEN_{lang_code.upper()}"
        token_env = os.getenv(secret_name)
        if not token_env: return
        token_data = json.loads(token_env)
        creds = Credentials.from_authorized_user_info(token_data)
        youtube = build("youtube", "v4", credentials=creds)
        body = {
            "snippet": {"title": meta['title'], "description": meta['description'], "tags": meta['tags'], "categoryId": "27"},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
        }
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
        print(f"[{lang_code.upper()}] Yüklendi: {response['id']}")
    except Exception as e:
        print(f"YT {lang_code} Hatası: {e}")

async def create_video_for_lang(lang_code, data, folder_idx, local_folder):
    lang_data = data[lang_code]
    voice_file = f"voice_{lang_code}.mp3"
    await edge_tts.Communicate(lang_data['script'], lang_data['voice']).save(voice_file)
    voice_audio = AudioFileClip(voice_file)
    
    # Görsel Hazırlama
    image_files = [os.path.join(local_folder, f"{i}{ext}") for i in range(1, 13) 
                   for ext in [".png", ".jpg", ".jpeg", ".PNG", ".JPG"] 
                   if os.path.exists(os.path.join(local_folder, f"{i}{ext}"))]
    if not image_files: raise Exception("Görsel bulunamadı")
    
    random.shuffle(image_files)
    
    clips = []
    current_time = 0.0
    img_dur = 4.0 
    while current_time < voice_audio.duration:
        img_path = image_files[len(clips) % len(image_files)]
        clip = process_image_to_fill(img_path).with_duration(img_dur).with_fps(24)
        clip = apply_random_ken_burns(clip)
        clips.append(clip)
        current_time += img_dur

    video = concatenate_videoclips(clips, method="compose").with_duration(voice_audio.duration)
    
    # METİN PARÇALAMA VE SENKRONİZASYON (Hatanın Çözümü)
    # Scripti cümlelere bölüyoruz (nokta, soru işareti vb.)
    sentences = re.split(r'(?<=[.!?]) +', lang_data['script'])
    text_clips = []
    
    # Her cümleye düşen süreyi hesapla
    avg_duration = voice_audio.duration / len(sentences)
    start_t = 0.0
    
    for sentence in sentences:
        if not sentence.strip(): continue
        txt = TextClip(
            text=sentence.strip(), 
            font_size=FONT_SIZE, 
            font=VIDEO_FONT, 
            color=FONT_COLOR, 
            method="caption", 
            size=(int(VIDEO_W * 0.8), None), 
            text_align="center",
            stroke_color="black",
            stroke_width=2
        ).with_duration(avg_duration).with_start(start_t).with_position(('center', 'center'))
        
        text_clips.append(txt)
        start_t += avg_duration

    bg_path = os.path.join(local_folder, "bg.mp3")
    bg = AudioFileClip(bg_path).with_effects([afx.AudioLoop(duration=voice_audio.duration)]).with_volume_scaled(0.1)
    
    final = CompositeVideoClip([video] + text_clips).with_audio(CompositeAudioClip([voice_audio, bg]))
    out = f"final_{lang_code}_{folder_idx}.mp4"
    final.write_videofile(out, codec="libx264", audio_codec="aac", bitrate="5000k")
    return out, lang_data

async def main():
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
