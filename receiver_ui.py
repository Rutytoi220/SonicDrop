import customtkinter as ctk
import threading
import queue
import sys
import time
import math
import json
import re
import base64
import os
import sounddevice as sd
from tkinter import ttk

# Import the existing transceiver logic
from transceiver import AcousticNode

class QueueStdout:
    """Redirects stdout to a thread-safe queue."""
    def __init__(self, q):
        self.queue = q

    def write(self, text):
        # Avoid putting entirely empty strings unless it's a newline
        if text:
            self.queue.put(text)

    def flush(self):
        pass

class ReceiverUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("Acoustic Data Receiver")
        self.geometry("650x550")
        
        # Set dark mode constraint
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        # Grid weights to make the text box scale
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)
        
        # 0.5 Device Selectors
        self.device_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.device_frame.grid(row=0, column=0, padx=20, pady=(20, 0), sticky="ew")
        self.device_frame.grid_columnconfigure(1, weight=1)
        
        try:
            devices = sd.query_devices()
            input_devices = [f"[{i}] {d['name']}" for i, d in enumerate(devices) if d['max_input_channels'] > 0]
            output_devices = [f"[{i}] {d['name']}" for i, d in enumerate(devices) if d['max_output_channels'] > 0]
        except Exception as e:
            input_devices = ["None"]
            output_devices = ["None"]
            print(f"[Error] Failed to query audio devices: {e}")
        
        if not input_devices: input_devices = ["None"]
        if not output_devices: output_devices = ["None"]
        
        self.mic_label = ctk.CTkLabel(self.device_frame, text="Microphone:")
        self.mic_label.grid(row=0, column=0, padx=(0, 10), pady=5, sticky="w")
        self.mic_dropdown = ttk.Combobox(self.device_frame, values=input_devices, state="readonly", width=50)
        if input_devices and input_devices[0] != "None":
            self.mic_dropdown.set(input_devices[0])
        self.mic_dropdown.grid(row=0, column=1, pady=5, sticky="ew")
        
        self.speaker_label = ctk.CTkLabel(self.device_frame, text="Speaker:")
        self.speaker_label.grid(row=1, column=0, padx=(0, 10), pady=5, sticky="w")
        self.speaker_dropdown = ttk.Combobox(self.device_frame, values=output_devices, state="readonly", width=50)
        if output_devices and output_devices[0] != "None":
            self.speaker_dropdown.set(output_devices[0])
        self.speaker_dropdown.grid(row=1, column=1, pady=5, sticky="ew")

        # Load saved settings
        try:
            if os.path.exists("settings.json"):
                with open("settings.json", "r") as f:
                    settings = json.load(f)
                    saved_mic = settings.get("mic")
                    saved_speaker = settings.get("speaker")
                    if saved_mic in input_devices:
                        self.mic_dropdown.set(saved_mic)
                    if saved_speaker in output_devices:
                        self.speaker_dropdown.set(saved_speaker)
        except Exception as e:
            print(f"[Warning] Could not load settings: {e}")

        # 1. Start/Stop Buttons Frame
        self.button_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.button_frame.grid(row=1, column=0, padx=20, pady=20, sticky="ew")
        self.button_frame.grid_columnconfigure(0, weight=1)
        self.button_frame.grid_columnconfigure(1, weight=1)

        self.start_button = ctk.CTkButton(
            self.button_frame, 
            text="Start Listening", 
            command=self.start_listening_thread,
            font=("Arial", 16, "bold"),
            height=45
        )
        self.start_button.grid(row=0, column=0, padx=(0, 10), sticky="ew")

        self.stop_button = ctk.CTkButton(
            self.button_frame, 
            text="Stop", 
            command=self.stop_listening,
            font=("Arial", 16, "bold"),
            height=45,
            fg_color="#dc3545",
            hover_color="#c82333"
        )
        self.stop_button.grid(row=0, column=1, padx=(10, 0), sticky="ew")
        
        # 1.2. Mic Level progress bar
        self.mic_level_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.mic_level_frame.grid(row=2, column=0, padx=20, pady=(0, 10), sticky="ew")
        self.mic_level_frame.grid_columnconfigure(1, weight=1)
        
        self.mic_level_label = ctk.CTkLabel(self.mic_level_frame, text="Mic Level:")
        self.mic_level_label.grid(row=0, column=0, padx=(0, 10))
        
        self.mic_level_bar = ctk.CTkProgressBar(self.mic_level_frame, mode="determinate")
        self.mic_level_bar.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.mic_level_bar.set(0.0)
        
        # 1.5. Progress bar and label frame
        self.progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.progress_frame.grid(row=3, column=0, padx=20, pady=(0, 10), sticky="ew")
        self.progress_frame.grid_columnconfigure(0, weight=1)
        
        self.progress_bar = ctk.CTkProgressBar(self.progress_frame, mode="determinate")
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.progress_bar.set(0.0)
        
        self.progress_label = ctk.CTkLabel(self.progress_frame, text="0%", font=("Arial", 14, "bold"))
        self.progress_label.grid(row=0, column=1)
        
        # 2. Large, scrollable text box (Console)
        self.console = ctk.CTkTextbox(
            self, 
            font=("Consolas", 14), 
            wrap="word", 
            state="disabled",
            fg_color="#1e1e1e",
            text_color="#00ff00"
        )
        self.console.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="nsew")
        
        # Thread-safe logging queue
        self.log_queue = queue.Queue()
        
        # Redirect sys.stdout
        self.original_stdout = sys.stdout
        sys.stdout = QueueStdout(self.log_queue)
        
        # State variables
        self.node = None
        self.listening_thread = None
        self.is_running = False
        
        # Stateless Chunk Accumulator
        self.received_chunks = {}
        self.total_chunks = 0
        
        print("[System] Application started. Ready to listen for acoustic data.")
        
        # Start GUI queue polling
        self.process_log_queue()
        
    def log_to_console(self, text):
        """Safely appends text to the GUI console and updates the progress bar if chunk headers exist."""
        
        if text.strip() == "CALIBRATING":
            self.start_button.configure(text="Calibrating...", fg_color="#ffcc00")
            return
            
        if text.strip() == "CALIBRATION_COMPLETE":
            self.start_button.configure(text="Listening for data...", fg_color="#28a745")
            return
            
        # Parse droplet progress (Fountain Code progress)
        droplet_match = re.search(r'DROPLET (\d+)/(\d+)', text)
        if droplet_match:
            collected = int(droplet_match.group(1))
            required = int(droplet_match.group(2))
            pct = collected / required if required > 0 else 0
            self.progress_bar.set(pct)
            self.progress_label.configure(text=f"{int(pct * 100)}%")
            return # Don't log this to console to prevent spam
        
        start_match = re.search(r'\[START:(\d+):(\d+)\]', text)
        if start_match:
            total_chunks = start_match.group(1)
            file_size = start_match.group(2)
            self.total_chunks = int(total_chunks)
            self.received_chunks.clear()
            self.progress_bar.set(0.0)
            self.progress_label.configure(text="0%")
            text = f"\n>>> INCOMING FILE: {file_size} bytes ({total_chunks} chunks) <<<\n{text}"
            
        # Parse chunk payload for the accumulator
        chunk_data_match = re.search(r'CHUNK (\d+)/(\d+): (.*)', text)
        if chunk_data_match:
            current = int(chunk_data_match.group(1))
            total = int(chunk_data_match.group(2))
            data = chunk_data_match.group(3).strip()
            
            self.total_chunks = total
            self.received_chunks[current] = data
            
            # Check for completion
            if len(self.received_chunks) == total:
                # Use after() to avoid blocking GUI thread inside logging hook
                self.after(10, self.save_file)
                
        # Append remaining text to the console
        self.console.configure(state="normal")
        self.console.insert("end", text)
        self.console.see("end")
        self.console.configure(state="disabled")

    def save_file(self):
        try:
            # Sort chunks to ensure correct sequence
            sorted_keys = sorted(self.received_chunks.keys())
            b64_str = "".join([self.received_chunks[k] for k in sorted_keys])
            
            file_data = base64.b64decode(b64_str)
            timestamp = int(time.time())
            filename = f"received_file_{timestamp}.txt"
            
            with open(filename, "wb") as f:
                f.write(file_data)
                
            # Log directly
            self.console.configure(state="normal")
            self.console.insert("end", f"\n\n[SUCCESS] File fully reconstructed and saved as '{filename}'!\n")
            self.console.see("end")
            self.console.configure(state="disabled")
            
            # Reset accumulator
            self.received_chunks.clear()
            self.total_chunks = 0
            
        except Exception as e:
            self.console.configure(state="normal")
            self.console.insert("end", f"\n\n[ERROR] Failed to save reconstructed file: {e}\n")
            self.console.see("end")
            self.console.configure(state="disabled")

    def process_log_queue(self):
        """Periodically checks the queue for new log messages."""
        while not self.log_queue.empty():
            try:
                msg = self.log_queue.get_nowait()
                self.log_to_console(msg)
            except queue.Empty:
                break
        
        # Schedule the next poll in 50ms
        self.after(50, self.process_log_queue)

    def transceiver_worker(self, mic_id, speaker_id):
        """Runs the transceiver logic in a background thread."""
        try:
            self.node = AcousticNode(input_device=mic_id, output_device=speaker_id)
            
            # The callback required by listen()
            # The backend already prints the chunk and data nicely to stdout,
            # which is now captured by our queue. We can leave this as a no-op or add it.
            def on_receive(text):
                print(text)
                
            def on_volume_update(rms):
                self.after(0, self.update_mic_level, rms)
                
            self.node.listen(callback=on_receive, on_volume_update=on_volume_update)
            
            # Keep the background thread alive so the stream context is held open
            while self.is_running:
                time.sleep(0.5)
                
        except Exception as e:
            print(f"[Error] Transceiver failed: {e}")
        finally:
            if self.node:
                self.node.stop_listening()

    def update_mic_level(self, rms):
        # Scale raw RMS value to a 0.0-1.0 float.
        # Float32 data usually has RMS around 0-0.5, but if scaled up to int16 (max 32767)
        # we assume speech could be around 2000-8000. 
        # Using 10000.0 as a reasonable soft maximum for the level meter.
        normalized = min(rms / 10000.0, 1.0)
        if math.isnan(normalized) or math.isinf(normalized):
            normalized = 0.0
        self.mic_level_bar.set(normalized)

    def stop_listening(self):
        if not self.is_running:
            return
        self.is_running = False
        if self.node:
            self.node.stop_listening()
        self.start_button.configure(state="normal", text="Start Listening")

    def start_listening_thread(self):
        """Triggered by the button. Spawns the worker thread safely."""
        if self.is_running:
            return
            
        # Parse selected devices
        mic_selection = self.mic_dropdown.get()
        speaker_selection = self.speaker_dropdown.get()
        
        try:
            mic_id = int(re.search(r'\[(\d+)\]', mic_selection).group(1))
            speaker_id = int(re.search(r'\[(\d+)\]', speaker_selection).group(1))
        except (AttributeError, ValueError):
            print("[Error] Invalid device selected.")
            return
            
        try:
            with open("settings.json", "w") as f:
                json.dump({"mic": mic_selection, "speaker": speaker_selection}, f)
        except Exception as e:
            print(f"[Warning] Could not save settings: {e}")
            
        self.start_button.configure(state="disabled")
        self.progress_bar.set(0.0) # Reset on fresh listen
        self.progress_label.configure(text="0%")
        self.is_running = True
        
        self.listening_thread = threading.Thread(target=self.transceiver_worker, args=(mic_id, speaker_id), daemon=True)
        self.listening_thread.start()

    def destroy(self):
        """Restore stdout and clean up threads on GUI exit."""
        sys.stdout = self.original_stdout
        self.is_running = False
        if self.node:
            self.node.stop_listening()
        super().destroy()

if __name__ == "__main__":
    app = ReceiverUI()
    app.mainloop()
