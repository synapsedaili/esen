import os
import json
import asyncio
import random
from moviepy import ImageClip, AudioFileClip, concatenate_videoclips, CompositeAudioClip, TextClip, CompositeVideoClip
import moviepy.video.fx as vfx
import moviepy.audio.fx as afx
import edge_tts
from huggingface_hub import snapshot_download
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

# --- GENEL AYARLAR ---
HF_REPO = os.getenv("HF_REPO_ID")
HF_TOKEN = os.getenv("HF_TOKEN")
VIDEO_FONT = "Liberation-Sans-Bold" 
FONT_COLOR = "yellow"
FONT_SIZE = 42

def apply_random_ken_burns(clip, loop_idx, is_first=False):
    if is_first or random.random() > 0.7:
        return clip
    zoom_val = random.uniform(1.1, 1.3)
    if loop_idx % 2 == 0:
        start_zoom, end_zoom = 1.0, zoom_val
    else:
        start_zoom, end_zoom = zoom_val, 1.0
    return clip.with_effects([vfx.Resize(lambda t: start_zoom + (end_zoom - start_zoom) * (t / clip.duration))])

def upload_to_youtube(video_path, meta, lang_code):
    try:
        secret_name = f"YT_TOKEN_{lang_code.upper()}"
        token_env = os.getenv(secret_name)
        if not token_env:
            print(f"Hata: {secret_name} secret'ı bulunamadı!")
            return
        token_data = json.loads(token_env)
        creds = Credentials.from_authorized_user_info(token_data)
        youtube = build("youtube", "v3", credentials=creds)
        body = {
            "snippet": {"title": meta['title'], "description": meta['description'], "tags": meta['tags'], "categoryId": "27"},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
        }
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
        print(f"[{lang_code.upper()}] Yüklendi! ID: {response['id']}")
    except Exception as e:
        print(f"YouTube {lang_code} yükleme hatası: {e}")

async def create_video_for_lang(lang_code, data, folder_idx, local_folder):
    lang_data = data[lang_code]
    voice_file = f"voice_{lang_code}.mp3"
    await edge_tts.Communicate(lang_data['script'], lang_data['voice']).save(voice_file)
    voice_audio = AudioFileClip(voice_file)
    image_files = []
    for i in range(1, 13):
        for ext in [".png", ".jpg", ".jpeg", ".PNG", ".JPG"]:
            path = os.path.join(local_folder, f"{i}{ext}")
            if os.path.exists(path):
                image_files.append(path)
                break
    if not image_files:
        raise Exception(f"Görsel bulunamadı: {local_folder}")
    img_duration = 4.0
    clips = []
    current_time = 0.0
    loop_idx = 0
    while current_time < voice_audio.duration:
        img_path = image_files[loop_idx % len(image_files)]
        clip = ImageClip(img_path).with_duration(img_duration).with_fps(24)
        clip = apply_random_ken_burns(clip, loop_idx, is_first=(current_time == 0.0))
        clips.append(clip)
        current_time += img_duration
        loop_idx += 1
    video = concatenate_videoclips(clips, method="compose").with_duration(voice_audio.duration)
    txt_clip = TextClip(text=lang_data['script'], font_size=FONT_SIZE, font=VIDEO_FONT, color=FONT_COLOR, method="caption", size=(video.w, None), text_align="center")
    txt_clip = txt_clip.with_duration(voice_audio.duration).with_position(('center', 'bottom'))
    bg_path = os.path.join(local_folder, "bg.mp3")
    bg_music = AudioFileClip(bg_path).with_effects([afx.AudioLoop(duration=voice_audio.duration)]).with_volume_scaled(0.1)
    final_video = CompositeVideoClip([video, txt_clip]).with_audio(CompositeAudioClip([voice_audio, bg_music]))
    output_name = f"final_{lang_code}_{folder_idx}.mp4"
    final_video.write_videofile(output_name, codec="libx264", audio_codec="aac")
    return output_name, lang_data

async def main():
    if not os.path.exists("current_index.txt"):
        with open("current_index.txt", "w") as f: f.write("1")
    with open("current_index.txt", "r") as f:
        idx = f.read().strip()
    
    local_dir = "temp_assets"
    os.makedirs(local_dir, exist_ok=True)
    
    try:
        # Klasör yapısını kontrol ederek indir
        snapshot_download(repo_id=HF_REPO, repo_type="dataset", allow_patterns=f"*/{idx}/*", token=HF_TOKEN, local_dir=local_dir)
        
        # Hugging Face klasör yapısı farklı gelebilir, o yüzden dinamik buluyoruz
        folder_path = None
        for root, dirs, files in os.walk(local_dir):
            if root.endswith(str(idx)) and "data.json" in files:
                folder_path = root
                break
        
        if not folder_path:
            raise Exception(f"Hugging Face'de {idx} numaralı klasör veya içindeki data.json bulunamadı!")

        with open(os.path.join(folder_path, "data.json"), 'r', encoding='utf-8') as f:
            all_data = json.load(f)

        v_es, m_es = await create_video_for_lang("es", all_data, idx, folder_path)
        upload_to_youtube(v_es, m_es, "es")
        v_en, m_en = await create_video_for_lang("en", all_data, idx, folder_path)
        upload_to_youtube(v_en, m_en, "en")

        with open("current_index.txt", "w") as f:
            f.write(str(int(idx) + 1))
    except Exception as e:
        print(f"Kritik Hata: {e}")

if __name__ == "__main__":
    asyncio.run(main())
