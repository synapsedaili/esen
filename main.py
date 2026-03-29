import os
import json
import asyncio
import random
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips, CompositeAudioClip, TextClip, CompositeVideoClip
import moviepy.audio.fx.all as afx
import edge_tts
from huggingface_hub import snapshot_download
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

# --- AYARLAR ---
HF_REPO = os.getenv("HF_REPO_ID")
HF_TOKEN = os.getenv("HF_TOKEN")
VIDEO_FONT = "Liberation-Sans" 
FONT_COLOR = "yellow"
FONT_SIZE = 40

def apply_ken_burns(clip, is_first=False):
    """Görsele rastgele Ken Burns (Zoom) efekti uygular."""
    if is_first:
        return clip # İlk görsel her zaman sabit
    
    # Görsellerin %65'ine rastgele efekt uygula
    if random.random() > 0.65:
        return clip

    # Rastgele başlangıç ve bitiş zoom değerleri (1.0 - 1.3 arası)
    start_zoom = random.uniform(1.0, 1.1)
    end_zoom = random.uniform(1.2, 1.4)
    
    # Zoom yönünü bazen tersine çevir (zoom-in veya zoom-out)
    if random.choice([True, False]):
        start_zoom, end_zoom = end_zoom, start_zoom

    return clip.resize(lambda t: start_zoom + (end_zoom - start_zoom) * (t / clip.duration))

def upload_to_youtube(video_path, meta):
    """Videoyu YouTube'a GİZLİ olarak yükler."""
    try:
        token_data = json.loads(os.getenv("YT_TOKEN_DATA"))
        creds = Credentials.from_authorized_user_info(token_data)
        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": meta['title'],
                "description": meta['description'],
                "tags": meta['tags'],
                "categoryId": "27"
            },
            "status": {
                "privacyStatus": "private",
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        
        response = None
        while response is None:
            status, response = request.next_chunk()
        print(f"Yüklendi! ID: {response['id']}")
    except Exception as e:
        print(f"YouTube hatası: {e}")

async def create_video_for_lang(lang_code, data, folder_idx, local_folder):
    lang_data = data[lang_code]
    print(f"--- {lang_code.upper()} Hazırlanıyor ---")

    voice_file = f"voice_{lang_code}.mp3"
    await edge_tts.Communicate(lang_data['script'], lang_data['voice']).save(voice_file)
    voice_audio = AudioFileClip(voice_file)

    # 1. Mevcut tüm görselleri listele (png, jpg, jpeg)
    image_files = []
    for i in range(1, 13):
        for ext in [".png", ".jpg", ".jpeg", ".PNG", ".JPG"]:
            path = os.path.join(local_folder, f"{i}{ext}")
            if os.path.exists(path):
                image_files.append(path)
                break
    
    # 2. Döngüsel Görsel Listesi Oluştur
    # Her görsel 4 saniye kalsın. Toplam süre dolana kadar listeyi uzatıyoruz.
    img_duration = 4 
    clips = []
    current_time = 0
    img_idx = 0
    
    while current_time < voice_audio.duration:
        # Görseli al (liste bitince başa dön)
        img_path = image_files[img_idx % len(image_files)]
        clip = ImageClip(img_path).set_duration(img_duration).set_fps(24)
        
        # Efekti uygula (ilk görsel ise is_first=True)
        is_first = (current_time == 0)
        clip = apply_ken_burns(clip, is_first=is_first)
        
        clips.append(clip)
        current_time += img_duration
        img_idx += 1

    video = concatenate_videoclips(clips, method="compose").set_duration(voice_audio.duration)

    # 3. Metin ve Müzik Ekleme
    txt_clip = TextClip(lang_data['script'], fontsize=FONT_SIZE, font=VIDEO_FONT, 
                        color=FONT_COLOR, method="caption", size=(video.w, None), align="south")
    txt_clip = txt_clip.set_duration(voice_audio.duration).set_position(('center', 'bottom'))
    
    bg_path = os.path.join(local_folder, "bg.mp3")
    bg_music = AudioFileClip(bg_path)
    bg_music = afx.audio_loop(bg_music, duration=voice_audio.duration).volumex(0.1)

    final_audio = CompositeAudioClip([voice_audio, bg_music])
    final_video = CompositeVideoClip([video, txt_clip]).set_audio(final_audio)

    output_name = f"final_{lang_code}_{folder_idx}.mp4"
    final_video.write_videofile(output_name, codec="libx264", audio_codec="aac")
    
    return output_name, lang_data

async def main():
    if not os.path.exists("current_index.txt"):
        with open("current_index.txt", "w") as f: f.write("1")

    with open("current_index.txt", "r") as f:
        idx = f.read().strip()

    target_subfolder = f"esen/{idx}"
    local_dir = "temp_assets"
    
    try:
        snapshot_download(repo_id=HF_REPO, repo_type="dataset", allow_patterns=f"{target_subfolder}/*", 
                          token=HF_TOKEN, local_dir=local_dir)
        
        folder_path = os.path.join(local_dir, "esen", idx)
        with open(os.path.join(folder_path, "data.json"), 'r', encoding='utf-8') as f:
            all_data = json.load(f)

        # ÖNCE İspanyolca Üret ve Yükle 
        video_es, meta_es = await create_video_for_lang("es", all_data, idx, folder_path)
        upload_to_youtube(video_es, meta_es)

        # SONRA İngilizce Üret ve Yükle 
        video_en, meta_en = await create_video_for_lang("en", all_data, idx, folder_path)
        upload_to_youtube(video_en, meta_en)

        # Başarılıysa indexi artır
        with open("current_index.txt", "w") as f:
            f.write(str(int(idx) + 1))

    except Exception as e:
        print(f"HATA: {e}")

if __name__ == "__main__":
    asyncio.run(main())
