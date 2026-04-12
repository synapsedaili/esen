import os
import tempfile
import json
import shutil
from pathlib import Path
import numpy as np
import asyncio
import edge_tts

# Video Kütüphaneleri
from moviepy.editor import ColorClip, CompositeVideoClip, AudioFileClip, ImageClip
from PIL import Image, ImageDraw, ImageFont

# Google API Kütüphaneleri
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

# ======================
# AYARLAR
# ======================
TOPIC = "1961"

SHORTS_SCRIPT = """
The Sovi""".strip() # Buraya kendi scriptini tam olarak koyabilirsin.

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

SCOPES = ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube']


# ======================
# YARDIMCI FONKSİYONLAR
# ======================
async def generate_voice_with_edge_tts(text: str, output_path: str):
    print(f"🎧 Edge TTS ile ses üretiliyor...")
    communicate = edge_tts.Communicate(text, "en-US-GuyNeural")
    await communicate.save(output_path)
    print(f"✅ Ses dosyası hazır: {output_path}")


def create_text_image_shorts(text: str, width: int, height: int, fontsize: int = 70) -> Image.Image:
    # Saydam arka plan
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        # Windows kullanıyorsan arial.ttf kalabilir, Linux ise default'a düşer
        font = ImageFont.truetype("arial.ttf", fontsize)
    except:
        font = ImageFont.load_default()

    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        test_line = current_line + (" " + word if current_line else word)
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] < width * 0.8:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word
    lines.append(current_line)

    total_height = len(lines) * (fontsize + 20)
    y = (height - total_height) // 2

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (width - (bbox[2] - bbox[0])) // 2
        # Yazı rengi sarı (yellow) yapıldı, siyah kontur eklendi
        draw.text((x, y), line, font=font, fill=(255, 255, 0, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
        y += fontsize + 20
    return img


# ======================
# YOUTUBE FONKSİYONLARI
# ======================
def get_authenticated_service():
    creds = None
    if os.path.exists('token.json'):
        with open('token.json', 'r') as token:
            creds_data = json.load(token)
            creds = Credentials.from_authorized_user_info(creds_data, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("🔄 Token süresi dolmuş, yenileniyor...")
            creds.refresh(Request())
        else:
            print("🔑 Yeni kimlik doğrulama başlatılıyor...")
            flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', SCOPES)
            creds = flow.run_local_server(port=0, access_type='offline', prompt='consent')

        with open('token.json', 'w') as token:
            token.write(creds.to_json())
            print("💾 Token kaydedildi.")

    return build('youtube', 'v3', credentials=creds)


def upload_to_youtube(video_path, title, description):
    youtube = get_authenticated_service()
    body = {
        'snippet': {'title': title, 'description': description, 'tags': ['history', 'shorts'], 'categoryId': '22'},
        'status': {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False}
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part=','.join(body.keys()), body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status: print(f"📈 Yükleme: %{int(status.progress() * 100)}")

    print(f"✅ Video yüklendi! ID: {response['id']}")


# ======================
# VİDEO OLUŞTURMA (DÜZELTİLEN KISIM)
# ======================
def create_video(script: str, output_path: str):
    print(f"🎥 Shorts üretiliyor...")
    temp_dir = Path(tempfile.mkdtemp())
    temp_audio = temp_dir / "temp_audio.mp3"

    asyncio.run(generate_voice_with_edge_tts(script, str(temp_audio)))
    audio = AudioFileClip(str(temp_audio))
    duration = min(audio.duration, 59)

    width, height = 1080, 1920
    # Arka plan rengini hafif koyu yaptık
    background = ColorClip((width, height), (15, 15, 15), duration=duration)

    words = script.split()
    # İSTEĞİN ÜZERİNE: 3 kelimelik gruplar yapıldı
    chunks = [" ".join(words[i:i + 3]) for i in range(0, len(words), 3)]

    text_clips = []
    chunk_duration = duration / len(chunks)
    
    for i, chunk in enumerate(chunks):
        start_t = i * chunk_duration
        if start_t >= duration: break
        
        # Her parça için ayrı görsel oluştur
        text_img = create_text_image_shorts(chunk, width, height)
        img_path = temp_dir / f"text_{i}.png"
        text_img.save(str(img_path))
        
        # Süreyi ve başlangıcı net belirledik ki üst üste binmesin
        txt_clip = (ImageClip(str(img_path))
                    .set_start(start_t)
                    .set_duration(chunk_duration)
                    .set_position('center'))
        text_clips.append(txt_clip)

    # Ses ve tüm yazı kliplerini birleştir
    final_video = CompositeVideoClip([background] + text_clips).set_audio(audio).set_duration(duration)
    
    # Render ayarları
    final_video.write_videofile(str(output_path), fps=24, audio_codec="aac", logger=None, threads=4)
    
    shutil.rmtree(temp_dir)
    return str(output_path)


if __name__ == "__main__":
    shorts_path = OUTPUT_DIR / "single_shorts.mp4"
    create_video(SHORTS_SCRIPT, shorts_path)
    upload_to_youtube(str(shorts_path), f"{TOPIC} #Shorts", "Automated History Shorts")
