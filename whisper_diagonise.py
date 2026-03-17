import static_ffmpeg
static_ffmpeg.add_paths()

import whisper
import sounddevice as sd
from scipy.io.wavfile import write
import time
import numpy as np

SAMPLE_RATE = 16000
DEVICE_INDEX = 1

print("🎤 Recording in 3 seconds...")
print("3..."); time.sleep(1)
print("2..."); time.sleep(1)
print("1..."); time.sleep(1)
print("🔴 Speak NOW - say 'Open Gmail' slowly and clearly!")

audio = sd.rec(
    int(6 * SAMPLE_RATE),  # 6 seconds now
    samplerate=SAMPLE_RATE,
    channels=1,
    dtype='int16',
    device=DEVICE_INDEX
)
sd.wait()

# Check audio level
max_amp = np.max(np.abs(audio))
print(f"\n📊 Max amplitude: {max_amp}")

write("test_audio.wav", SAMPLE_RATE, audio)

print("⏳ Loading Whisper...")
model = whisper.load_model("base")

# Try multiple approaches
print("\n--- Attempt 1: Default ---")
result1 = model.transcribe("test_audio.wav", fp16=False)
print(f"Result: '{result1['text']}'")

print("\n--- Attempt 2: English forced ---")
result2 = model.transcribe("test_audio.wav", fp16=False, language="en")
print(f"Result: '{result2['text']}'")

print("\n--- Attempt 3: With beam search ---")
result3 = model.transcribe("test_audio.wav", fp16=False, language="en", beam_size=5)
print(f"Result: '{result3['text']}'")

print("\n--- Attempt 4: Small model ---")
model2 = whisper.load_model("small")
result4 = model2.transcribe("test_audio.wav", fp16=False, language="en")
print(f"Result: '{result4['text']}'")