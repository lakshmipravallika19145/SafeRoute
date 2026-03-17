import static_ffmpeg
static_ffmpeg.add_paths()

import whisper
import sounddevice as sd
from scipy.io.wavfile import write
import time
import numpy as np

SAMPLE_RATE = 16000
DEVICE_INDEX = 1

# Load model once
print("⏳ Loading Whisper model...")
model = whisper.load_model("base")
print("✅ Whisper ready!\n")

def record_audio(duration=5):
    print("🎤 Listening in 3 seconds...")
    print("3..."); time.sleep(1)
    print("2..."); time.sleep(1)
    print("1..."); time.sleep(1)
    print("🔴 Speak your command NOW!")
    
    audio = sd.rec(
        int(duration * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype='int16',
        device=DEVICE_INDEX
    )
    sd.wait()
    write("command.wav", SAMPLE_RATE, audio)
    print("✅ Audio captured!")

def transcribe():
    result = model.transcribe(
        "command.wav",
        language="en",
        fp16=False,
        beam_size=5        # ✅ Best setting for your voice
    )
    text = result["text"].strip().lower()
    return text

# Test loop
print("="*40)
print("🚀 JarvisDesk Voice Test!")
print("="*40)
print("💡 Tips: Speak clearly, normal pace, close to mic\n")

while True:
    input("⏎ Press ENTER to speak a command...")
    record_audio()
    command = transcribe()
    print(f"\n📝 Command: '{command}'")
    print("-"*40)