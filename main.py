import os
import json
import asyncio
import random
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
# DAHA İYİ FONT: Stoacı havaya uygun tırnaklı (serif) bold font
VIDEO_FONT = "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf" 
FONT_COLOR = "yellow"
FONT_SIZE = 65  # Yazı boyutu büyütüldü
VIDEO_W, VIDEO_H = 1080, 1920 # Shorts Standardı

def process_image_to_fill(img_path):
    """Görseli açar ve 9:16 ekranı dolduracak şekilde büyütür/kırpar."""
    clip = ImageClip(img_path)
    
    # Ekran oranları
    screen_aspect_ratio = VIDEO_W / VIDEO_H
    
    # Resim oranları
    img_width, img_height = clip.w, clip.h
    img_aspect_ratio = img_width / img_height

    # Eğer resim yataysa veya kareyse, yüksekliği ekrana göre ayarla, yanlardan kırp
    if img_aspect_ratio > screen_aspect_ratio:
        new_w = int(VIDEO_H * img_aspect_ratio)
        clip = clip.resized(height=VIDEO_H, width=new_w) # resized v2 standardı
        clip = clip.cropped(x1=(clip.w - VIDEO_W)//2, y1=0, width=VIDEO_W, height=VIDEO_H)
    # Eğer resim dikeyse ama ekrandan daha dikeyse, genişliği ayarla, alt-üstten kırp
    else:
        new_h = int(VIDEO_W / img_aspect_ratio)
        clip = clip.resized(width=VIDEO_W, height=new_h)
        clip = clip.cropped(x1=0, y1=(clip.h - VIDEO_H)//2, width=VIDEO_W, height=VIDEO_H)
        
    return clip

def apply_random_ken_burns(clip):
    """
    Karekökünden değiştirildi!
    Görsele rastgele yönlü ve rastgele hızlı kayma (pan) ve büyüme (zoom) efekti uygular.
    """
    duration = clip.duration
    zoom_speed = random.uniform(0.01, 0.03) # Ne kadar büyüyecek
    
    # Rastgele başlangıç ve bitiş noktaları (Görsel ekranı tam kapladığı için kırpma alanını rastgele kaydırıyoruz)
    # Başlangıçta 1.1x büyütüp, ekranın rastgele bir yerinden başlatıp rastgele bitiriyoruz.
    
    # 0.0 ile 0.1 arasında rastgele başlangıç pozisyonları
    start_x = random.uniform(0.0, 0.1)
    start_y = random.uniform(0.0, 0.1)
    
    # Rastgele yönlü bitiş pozisyonları
    end_x = random.uniform(0.0, 0.1)
    end_y = random.uniform(0.0, 0.1)

    # MoviePy v2 ile efekt uygulama (vfx.Resize ve vfx.Crop kullanılarak rastgele hareket simülasyonu)
    return clip.with_effects([
        vfx.Resize(lambda t: 1.1 + zoom_speed * t), # Sürekli yavaşça büyür
        vfx.Crop(x=lambda t: start_x * clip.w + (end_x - start_x) * clip.w * (t/duration), # Rastgele Pan (Yatay Kayma)
                 y=lambda t: start_y * clip.h + (end_y - start_y) * clip.h * (t/duration), # Rastgele Pan (Dikey Kayma)
                 width=VIDEO_W, height=VIDEO_H)
    ])

def upload_to_youtube(video_path, meta, lang_code):
    try:
        secret_name = f"YT_TOKEN_{lang_code.upper()}"
        token_env = os.getenv(secret_name)
        if not token_env: return
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
        print(f"[{lang_code.upper()}] Yüklendi: {response['id']}")
    except Exception as e:
        print(f"YT {lang_code} Hatası: {e}")

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
    if not image_files: raise Exception(f"Görsel bulunamadı: {local_folder}")
    
    # Rastgele sırayla resimleri kullanmak için karıştırıyoruz
    shuffled_images = image_files.copy()
    random.shuffle(shuffled_images)
    
    clips = []
    current_time = 0.0
    img_idx = 0
    img_dur = 4.0 # Resim başına süre
    
    while current_time < voice_audio.duration:
        img_path = shuffled_images[img_idx % len(shuffled_images)]
        
        # 1. Resmi aç ve 9:16 dikey formatta ekranı kaplayacak şekilde işle
        clip = process_image_to_fill(img_path).with_duration(img_dur).with_fps(24)
        
        # 2. Tamamen rastgele Ken Burns efekti uygula
        clip = apply_random_ken_burns(clip)
        
        clips.append(clip)
        current_time += img_dur
        img_idx += 1

    # Resimleri birleştir
    video = concatenate_videoclips(clips, method="compose").with_duration(voice_audio.duration)
    
    # 3. Yazı Ayarları: Orta kısımda, büyük ve daha okunaklı font
    txt_w = int(video.w * 0.9)
    txt = TextClip(
        text=lang_data['script'], 
        font_size=FONT_SIZE, 
        font=VIDEO_FONT, 
        color=FONT_COLOR, 
        method="caption", 
        size=(txt_w, None), 
        text_align="center",
        stroke_color="black", # Yazının okunması için siyah kenarlık
        stroke_width=2
    )
    
    # Yazıyı EKRANIN ORTASINA alıyoruz ('center', 'center')
    txt = txt.with_duration(voice_audio.duration).with_position(('center', 'center'))
    
    bg_path = os.path.join(local_folder, "bg.mp3")
    bg = AudioFileClip(bg_path).with_effects([afx.AudioLoop(duration=voice_audio.duration)]).with_volume_scaled(0.1)
    
    final = CompositeVideoClip([video, txt]).with_audio(CompositeAudioClip([voice_audio, bg]))
    out = f"final_{lang_code}_{folder_idx}.mp4"
    
    # Yüksek kaliteli render için bitrate artırıldı
    final.write_videofile(out, codec="libx264", audio_codec="aac", bitrate="8000k")
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
