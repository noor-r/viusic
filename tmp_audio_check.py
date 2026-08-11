from pathlib import Path
import subprocess
from app.main import attach_mp3_to_video

base = Path('tmp-audio-check')
base.mkdir(exist_ok=True)
src = base / 'src.mp4'
music = base / 'music.mp3'
out = base / 'out.mp4'
subprocess.run(['ffmpeg','-y','-f','lavfi','-i','color=c=blue:s=160x120:d=2','-f','lavfi','-i','sine=frequency=1000:duration=2','-shortest',str(src)], check=True, capture_output=True, text=True)
subprocess.run(['ffmpeg','-y','-f','lavfi','-i','sine=frequency=500:duration=2','-shortest',str(music)], check=True, capture_output=True, text=True)
attach_mp3_to_video(src, music, out)
print('created', out.exists())
result = subprocess.run(['ffprobe','-v','error','-show_streams','-show_format',str(out)], check=True, capture_output=True, text=True)
print(result.stdout)
