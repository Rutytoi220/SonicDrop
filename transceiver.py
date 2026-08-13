import ggwave
import sounddevice as sd
import numpy as np
import base64
import time
import re
import os
import queue
import threading
import scipy.signal
import struct
import zlib

class AcousticNode:
    def __init__(self, tx_protocol=2, rx_protocol=2, input_device=None, output_device=None):
        """
        Initialize the AcousticNode with ggwave protocols.
        tx_protocol=2 is GGWAVE_PROTOCOL_AUDIBLE_FASTEST
        """
        self.tx_protocol = tx_protocol
        self.rx_protocol = rx_protocol
        self.input_device = input_device
        self.output_device = output_device
        
        self.samplerate = 48000
        
        # Configure ggwave for the default 48kHz hardware clock
        params = ggwave.getDefaultParameters()
        params['sampleRateInp'] = self.samplerate
        params['sampleRateOut'] = self.samplerate
        params['sampleRate'] = self.samplerate
        params['samplesPerFrame'] = 1024
        
        # 4 corresponds to GGWAVE_SAMPLE_FORMAT_I16 in ggwave C++ bindings
        params['sampleFormatInp'] = 4
        params['sampleFormatOut'] = 4
        
        self.instance = ggwave.init(params)
        
        self.stream = None
        self.is_listening = False
        
        self.audio_queue = queue.Queue()
        self.dsp_thread = None
        self.noise_floor = 0.0
        self.calibration_end_time = 0
        
        # To store incoming file chunks if needed in the future
        self.received_chunks = {}

    def send_string(self, text: str, block=True):
        """
        Encodes and transmits a string as audio.
        If block is True, waits until the audio finishes playing.
        """
        print(f"[AcousticNode] Encoding and sending: '{text}'")
        payload = ggwave.encode(text, protocolId=self.tx_protocol, instance=self.instance)
        audio = np.frombuffer(payload, dtype=np.int16)
        
        sd.play(audio, samplerate=self.samplerate, blocking=False, device=self.output_device)
        if block:
            sd.wait()

    def dsp_worker(self, native_rate, callback, on_volume_update=None):
        batch_frames = []
        calibration_buffer = []
        audio_buffer = bytearray()
        
        target_rate = 48000
        
        # Configure ggwave for strict 48kHz
        params = ggwave.getDefaultParameters()
        params['sampleRateInp'] = target_rate
        params['sampleRateOut'] = target_rate
        params['sampleRate'] = target_rate
        params['samplesPerFrame'] = 1024
        # params['soundMarkerThreshold'] = 10
        
        # 4 corresponds to GGWAVE_SAMPLE_FORMAT_I16
        params['sampleFormatInp'] = 4
        params['sampleFormatOut'] = 4
        self.instance = ggwave.init(params)
        
        while self.is_listening:
            try:
                indata = self.audio_queue.get(timeout=0.1)
                
                # 1. Downmix to mono if multi-channel
                if len(indata.shape) > 1 and indata.shape[1] > 1:
                    mono_data = np.mean(indata, axis=1)
                else:
                    mono_data = indata.flatten()
                    
                # Explicitly scale and cast to int16 immediately
                if mono_data.dtype == np.float32 or mono_data.dtype == np.float64:
                    mono_data = mono_data * 32767.0
                data = np.clip(mono_data, -32768, 32767).astype(np.int16)
                    
                # 2. Calibration phase
                if time.time() < self.calibration_end_time:
                    calibration_buffer.append(data)
                    continue
                elif self.calibration_end_time > 0 and self.noise_floor == 0.0:
                    calib_data = np.concatenate(calibration_buffer)
                    if len(calib_data) > 0:
                        self.noise_floor = float(np.sqrt(np.mean(calib_data.astype(np.float64)**2))) / 32768.0
                    else:
                        self.noise_floor = 0.001
                    print(f"\n[AcousticNode] Calibration complete. Noise floor: {self.noise_floor:.5f}")
                    callback("CALIBRATION_COMPLETE")
                
                # 3. Noise Gate
                rms = float(np.sqrt(np.mean(data.astype(np.float64)**2))) if len(data) > 0 else 0.0
                if on_volume_update:
                    on_volume_update(rms)
                    
                normalized_rms = rms / 32768.0
                if normalized_rms < self.noise_floor * 1.2:
                    data.fill(0)
                
                # 4. Batching and Resampling
                batch_frames.append(data)
                
                total_len = sum(len(x) for x in batch_frames)
                if total_len >= 4096:
                    raw_audio = np.concatenate(batch_frames)
                    batch_frames = []
                    
                    if native_rate != target_rate:
                        resampled = scipy.signal.resample_poly(raw_audio, target_rate, native_rate)
                    else:
                        resampled = raw_audio
                        
                    int16_audio = resampled.astype(np.int16)
                    # Calculate normalized volume of the chunk
                    chunk_rms = float(np.sqrt(np.mean(int16_audio.astype(np.float64)**2)))
                    chunk_vol = chunk_rms / 32768.0
                    print(f"[DEBUG] Chunk volume before decode: {chunk_vol:.5f}")
                    
                    if chunk_vol < self.noise_floor * 1.5:
                        int16_audio.fill(0)
                        
                    buffer = int16_audio.tobytes()
                    audio_buffer.extend(buffer)
                    
                    BYTES_PER_FRAME = 1024 * 2  # 1024 samples * 2 bytes/sample for int16
                    while len(audio_buffer) >= BYTES_PER_FRAME:
                        chunk = bytes(audio_buffer[:BYTES_PER_FRAME])
                        del audio_buffer[:BYTES_PER_FRAME]
                        
                        # 5. Decode FSK
                        res = ggwave.decode(self.instance, chunk)
                        if res:
                            print(f"[DEBUG] Decoder triggered END MARKER. Raw Hex: {res.hex()}")
                            self._process_ggwave_result(res, callback)
            except queue.Empty:
                pass
            except Exception as e:
                print(f"[AcousticNode] DSP Worker Error: {e}")

    def _process_ggwave_result(self, res, callback):
        try:
            # Unpack the first 4 bytes using struct
            if len(res) < 4:
                return
            
            chunk_idx, total_chunks = struct.unpack('<HH', res[:4])
            payload_data = res[4:]
            
            if total_chunks <= 0 or chunk_idx >= total_chunks:
                return
                
            if chunk_idx not in self.received_chunks:
                self.received_chunks[chunk_idx] = payload_data
                print(f"[DECODER SUCCESS] Payload chunk {chunk_idx+1}/{total_chunks} received.")
                
                # Emit DROPLET log so the UI progress bar updates seamlessly
                callback(f"DROPLET {len(self.received_chunks)}/{total_chunks}")
                
                # Check for completion
                if len(self.received_chunks) == total_chunks:
                    print("\n[System] All chunks received! Reconstructing file...")
                    
                    # Reassemble bytearray
                    full_compressed_data = bytearray()
                    for i in range(total_chunks):
                        full_compressed_data.extend(self.received_chunks[i])
                        
                    # Decompress
                    try:
                        decompressed_data = zlib.decompress(bytes(full_compressed_data))
                        
                        timestamp = int(time.time())
                        filename = f"received_file_{timestamp}.bin"
                        
                        with open(filename, "wb") as f:
                            f.write(decompressed_data)
                            
                        print(f"[SUCCESS] File fully reconstructed, decompressed, and saved as '{filename}'!")
                        callback(f"\n\n[SUCCESS] File fully reconstructed and saved as '{filename}'!\n")
                    except Exception as e:
                        print(f"[Error] Decompression failed: {e}")
                        callback(f"\n\n[ERROR] Decompression failed: {e}\n")
                        
                    # Reset accumulator
                    self.received_chunks.clear()
                    
        except Exception as e:
            # Silently ignore malformed noise packets
            pass

    def listen(self, callback, on_volume_update=None):
        """
        Starts a non-blocking audio stream dumping to a queue, processed by a DSP thread.
        """
        if self.is_listening:
            return

        def audio_callback(indata, frames, time_info, status):
            if status:
                print(f"[Audio Stream Status]: {status}")
            self.audio_queue.put(indata.copy())

        try:
            native_rate = int(sd.query_devices(self.input_device)['default_samplerate'])
            if native_rate <= 0:
                native_rate = 48000
        except Exception:
            native_rate = 48000
            
        self.samplerate = native_rate
        
        self.is_listening = True
        self.noise_floor = 0.0
        self.calibration_end_time = time.time() + 2.0
        
        self.dsp_thread = threading.Thread(target=self.dsp_worker, args=(native_rate, callback, on_volume_update), daemon=True)
        self.dsp_thread.start()
        
        try:
            device_info = sd.query_devices(self.input_device)
            native_channels = int(device_info['max_input_channels'])
        except Exception:
            native_channels = 1

        self.stream = sd.InputStream(
            samplerate=native_rate, channels=native_channels,
            callback=audio_callback, blocksize=1024, device=self.input_device
        )
        self.stream.start()
        callback("CALIBRATING")
        print("[AcousticNode] Calibrating room noise floor...")

    def stop_listening(self):
        self.is_listening = False
        if self.stream and self.stream.active:
            self.stream.stop()
        if self.dsp_thread:
            self.dsp_thread.join(timeout=1.0)
            self.stream.close()
        self.is_listening = False
        print("[AcousticNode] Stopped listening.")

if __name__ == "__main__":
    def on_receive(text):
        print(text)
        print("------------------------\n")
        
    node = AcousticNode()
    node.listen(on_receive)
    
    # Create a small dummy file to test Phase 2
    test_filepath = "test_payload.txt"
    with open(test_filepath, "w") as f:
        f.write("This is a small test file to demonstrate Phase 2 base64 encoding and chunking functionality! " * 5)
        
    time.sleep(1) # wait a moment for the microphone to warm up
    node.send_file(test_filepath)
    
    try:
        print("[Test] Keeping main thread alive for 5 seconds to receive the self-transmitted chunks...")
        time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop_listening()
        if os.path.exists(test_filepath):
            os.remove(test_filepath)
        print("Exiting...")
