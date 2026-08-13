import ggwave
import numpy as np
from scipy.io import wavfile
import os
import sys
import zlib
import struct

def generate_wav(input_file="test_payload.txt", output_file="transfer_payload.wav", samplerate=48000, input_data=None):
    if input_data is not None:
        file_data = input_data
    else:
        if not os.path.exists(input_file):
            print(f"Error: {input_file} not found. Please create it or run transceiver.py once to generate a test file.")
            sys.exit(1)
                
        print(f"Reading payload from {input_file}...")
        with open(input_file, "rb") as f:
            file_data = f.read()
        
    compressed_data = zlib.compress(file_data)
    print(f"Original size: {len(file_data)} bytes. Compressed size: {len(compressed_data)} bytes.")
    
    # 12-byte chunks (leaving 4 bytes for header to fit within 16-byte ultra-low bandwidth FSK)
    chunk_size = 12 
    chunks = [compressed_data[i:i+chunk_size] for i in range(0, len(compressed_data), chunk_size)]
    total_chunks = len(chunks)
    
    print(f"Split into {total_chunks} chunks.")
    
    # Initialize ggwave
    params = ggwave.getDefaultParameters()
    params['sampleRateInp'] = samplerate
    params['sampleRateOut'] = samplerate
    params['sampleRate'] = samplerate
    params['samplesPerFrame'] = 1024
    
    # 4 corresponds to GGWAVE_SAMPLE_FORMAT_I16
    params['sampleFormatInp'] = 4
    params['sampleFormatOut'] = 4
    
    instance = ggwave.init(params)
    full_audio = []
    
    # Generate 0.5s of silence (ISI gap to prevent data collision between chunks)
    silence = np.zeros(int(samplerate * 2.0), dtype=np.int16) # Increased ISI gap to 2.0s to allow room echo to decay
    
    carousel_passes = 4
    print(f"Encoding {total_chunks} chunks into Data Carousel ({carousel_passes} passes)...")
    
    for pass_idx in range(carousel_passes):
        for chunk_idx, chunk_data in enumerate(chunks):
            # 4-byte header: <HH (chunk_index, total_chunks)
            header = struct.pack('<HH', chunk_idx, total_chunks)
            binary_payload = header + chunk_data
            
            # Encode binary directly (ggwave supports string or bytes, but we pass string cast or bytes depending on binding)
            # Python ggwave decode returns bytes, so encode should accept bytes as well.
            payload = ggwave.encode(binary_payload, protocolId=2, volume=10, instance=instance)
            
            # Convert raw bytes into int16 array for WAV export
            audio = np.frombuffer(payload, dtype=np.int16)
            full_audio.append(audio)
            
            # Add silence between chunks
            full_audio.append(silence)
            
    # Concatenate all numpy arrays into a single continuous track
    final_waveform = np.concatenate(full_audio)
    
    # Export to WAV file
    print(f"Exporting to {output_file}...")
    if output_file:
        wavfile.write(output_file, samplerate, final_waveform)
    return final_waveform, samplerate
    
    duration = len(final_waveform) / samplerate
    print(f"Success! {output_file} generated. Length: {duration:.2f} seconds.")

if __name__ == "__main__":
    generate_wav()
