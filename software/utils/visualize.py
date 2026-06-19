from pathlib import Path
import numpy as np
import tqdm
import wave

# Target the current working directory
path = Path.cwd()

# Find all text files in the current folder
wav_files = list(path.glob("chunk*.wav"))
wav_files = sorted([temp.name for temp in wav_files])
print(wav_files)

first = int(wav_files[0].split('_')[1].split('.')[0])
last = int(wav_files[-1].split('_')[1].split('.')[0])

found = 0
total = 0
data = []
for val in tqdm.tqdm(list(range(first, last + 1, 2))):
    chunk = f'chunk_{val}.wav'
    if Path(chunk).exists():
        found += 1

        with wave.open(chunk, "rb") as wav_file:
            # 2. Extract metadata
            channels = wav_file.getnchannels()      # 1 for mono, 2 for stereo
            sample_width = wav_file.getsampwidth()  # Bytes per sample (e.g., 2 for 16-bit)
            sample_rate = wav_file.getframerate()   # Sampling frequency (e.g., 44100)
            total_frames = wav_file.getnframes()    # Total number of audio frames
            
            # print(f"Channels: {channels}")
            # print(f"Sample Width: {sample_width} bytes")
            # print(f"Sample Rate: {sample_rate} Hz")
            # print(f"Total Frames: {total_frames}")

            assert channels == 1
            assert sample_width == 2
            assert sample_rate == 384000
            assert total_frames == sample_rate * sample_width

            # 3. Read raw byte data
            raw_bytes = wav_file.readframes(total_frames)
            audio_data = np.frombuffer(raw_bytes, dtype=np.int16)
    else:
        audio_data = np.zeros(768000, dtype=np.int16)

    total += 1
    data.append(audio_data)

print(found, total)

data = np.hstack(data)
with wave.open('combined.wav', 'wb') as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(384000)
    wf.writeframes(data.tobytes())
