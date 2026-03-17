import sounddevice as sd

# List all available audio devices
print("🎤 Available Audio Devices:")
print(sd.query_devices())

# Show default input device
print("\n✅ Default Microphone:")
print(sd.query_devices(kind='input'))