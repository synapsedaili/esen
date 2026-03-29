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

# Font Ayarları: Liberation-Sans-Bold kalın ve okunaklıdır.
VIDEO_FONT = "Liberation-Sans-Bold" 
FONT_COLOR = "yellow" # Sarı
FONT_SIZE = 42 # Biraz daha büyük, net okunması için

def apply_random_ken_burns(clip, loop_idx, is_first=False):
    """Görsele rastgele ama in-out sırasına göre Ken Burns efekti uygular."""
    if is_first:
        return clip # İlk görsel sabit

    # Toplam efekt uygulanma oranı: %70 (%35 in, %35 out)
    effect_chance = 0.7 
    if random.random() > effect_chance:
        return clip # %30 ihtimalle sabit kalır

    # Rastgele zoom değerleri (1.1 - 1.3 arası)
    zoom_val = random.uniform(1.1, 1.3)
    
    # loop_idx tek mi çift mi olduğuna göre in-out sırasını belirle.
    # Bu, %35 in, %35 out oranını rastgelelik içinde korur.
    if loop_idx % 2 == 0:
        # ZOOM-IN: Normalden yakınlaşmaya (1.0 -> zoom_val)
        start_zoom = 1.0
        end_zoom = zoom_val
    else:
        # ZOOM-OUT: Yakından normale (zoom_val -> 1.0)
        start_zoom = zoom_val
        end_zoom = 1.0

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
                "categoryId": "27" # Eğitim
            },
            "status": {
                "privacyStatus": "private", # Şimdilik GİZLİ
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        
        response = None
        while response is None:
            status, response = request.next_chunk()
        print(f"Başarıyla yüklendi! ID: {response['id']}")
    except Exception as e:
        print(f"YouTube yükleme hatası: {e}")

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
    img_duration = 4.0 # Her görsel 4 saniye
    clips = []
    current_time = 0.0
    loop_idx = 0
    
    while current_time < voice_audio.duration:
        # Görseli al (liste bitince başa dön)
        img_path = image_files[loop_idx % len(image_files)]
        clip = ImageClip(img_path).set_duration(img_duration).set_fps(24)
        
        # Efekti uygula (ilk görsel ise is_first=True)
        is_first = (current_time == 0.0)
        clip = apply_random_ken_burns(clip, loop_idx, is_first=is_first)
        
        clips.append(clip)
        current_time += img_duration
        loop_idx += 1

    video = concatenate_videoclips(clips, method="compose").set_duration(voice_audio.duration)

    # 3. Metin ve Müzik Ekleme (Bold Sarı Font)
    txt_clip = TextClip(lang_data['script'], fontsize=FONT_SIZE, font=VIDEO_FONT, 
                        color=FONT_COLOR, method="caption", size=(video.w, None), align="south")
    txt_clip = txt_clip.set_duration(voice_audio.duration).set_position(('center', 'bottom'))
    
    bg_path = os.path.join(local_folder, "bg.mp3")
    bg_music = AudioFileClip(bg_path)
    # Arka plan müziğini video süresince döngüye sok (Loop) ve sesini düşür (%10)
    bg_music = afx.audio_loop(bg_music, duration=voice_audio.duration).volumex(0.1)

    final_audio = CompositeAudioClip([voice_audio, bg_music])
    # Videoyu ve metni birleştir, sesi ekle
    final_video = CompositeVideoClip([video, txt_clip]).set_audio(final_audio)

    output_name = f"final_{lang_code}_{folder_idx}.mp4"
    # YouTube uyumluluğu için aac codec
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
        # Sadece o günkü klasörü (esen/idx/*) indir
        snapshot_download(repo_id=HF_REPO, repo_type="dataset", allow_patterns=f"{target_subfolder}/*", 
                          token=HF_TOKEN, local_dir=local_dir)
        
        folder_path = os.path.join(local_dir, "esen", idx)
        
        # Hata Kontrolü: Klasör boşsa veya data.json yoksa hata ver
        json_path = os.path.join(folder_path, "data.json")
        if not os.path.exists(json_path):
            raise FileNotFoundError(f"data.json bulunamadı: {json_path}")

        with open(json_path, 'r', encoding='utf-8') as f:
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
        print(f"İşlem başarıyla tamamlandı. Index {idx}'den {int(idx)+1}'e güncellendi.")

    except Exception as e:
        print(f"SİSTEM HATASI: {e}")

if __name__ == "__main__":
    asyncio.run(main())
