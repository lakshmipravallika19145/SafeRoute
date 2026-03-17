import static_ffmpeg
static_ffmpeg.add_paths()

import subprocess
result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
print(result.stdout[:100])
print("✅ ffmpeg is working!")