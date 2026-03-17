import sounddevice as sd
from scipy.io.wavfile import read

sample_rate, data = read("test_audio.wav")
print(f"✅ Sample rate: {sample_rate}")
print(f"✅ Audio shape: {data.shape}")
print("🔊 Playing back your recording...")
sd.play(data, sample_rate)
sd.wait()
print("✅ Playback done!")