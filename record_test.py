import sounddevice as sd
from scipy.io.wavfile import write
import numpy as np

DURATION = 5      # seconds
SAMPLE_RATE = 16000  # Whisper works best at 16000

print("🎤 Recording starts in 3 seconds...")
print("3..."); import time; time.sleep(1)
print("2..."); time.sleep(1)
print("1..."); time.sleep(1)
print("🔴 Recording NOW! Speak something...")

# Record audio
audio = sd.rec(
    int(DURATION * SAMPLE_RATE),
    samplerate=SAMPLE_RATE,
    channels=1,
    dtype='int16'
)
sd.wait()  # Wait until recording finishes

# Save to file
write("test_audio.wav", SAMPLE_RATE, audio)
print("✅ Recording saved as test_audio.wav!")