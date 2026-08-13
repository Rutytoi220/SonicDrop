import customtkinter as ctk
from tkinter import ttk, filedialog
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
import traceback

from transceiver import AcousticNode
from generate_audio import generate_wav

class QueueStdout:
    """Redirects stdout to a thread-safe queue."""
    def __init__(self, q):
        self.queue = q

    def write(self, text):
        if text:
            self.queue.put(text)

    def flush(self):
        pass

class SonicDropUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("SonicDrop - Acoustic File Transfer")
        self.geometry("750x650")
        
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        # Grid layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        
        # --- 1. Global Device Selectors ---
        self.device_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.device_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        self.device_frame.grid_columnconfigure(1, weight=1)
        self.device_frame.grid_columnconfigure(3, weight=1)
        
        try:
            devices = sd.query_devices()
            default_in, default_out = sd.default.device
            
            input_devices = []
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0:
                    rate = d["default_samplerate"] / 1000.0
                    prefix = "(Recommended) " if i == default_in else ""
                    input_devices.append(f"{prefix}[{i}] {d['name']} ({rate:g}kHz)")
                    
            output_devices = []
            for i, d in enumerate(devices):
                if d["max_output_channels"] > 0:
                    rate = d["default_samplerate"] / 1000.0
                    prefix = "(Recommended) " if i == default_out else ""
                    output_devices.append(f"{prefix}[{i}] {d['name']} ({rate:g}kHz)")
            
            # Sort so recommended is at the top
            input_devices.sort(key=lambda x: not x.startswith("(Recommended)"))
            output_devices.sort(key=lambda x: not x.startswith("(Recommended)"))
            
        except Exception as e:
            input_devices = ["None"]
            output_devices = ["None"]
            print(f"[Error] Failed to query audio devices: {e}")
        
        if not input_devices: input_devices = ["None"]
        if not output_devices: output_devices = ["None"]
        
        # Mic
        self.mic_label = ctk.CTkLabel(self.device_frame, text="Microphone:")
        self.mic_label.grid(row=0, column=0, padx=(0, 10), pady=5, sticky="w")
        self.mic_dropdown = ttk.Combobox(self.device_frame, values=input_devices, state="readonly", width=30)
        if input_devices and input_devices[0] != "None":
            self.mic_dropdown.set(input_devices[0])
        self.mic_dropdown.grid(row=0, column=1, pady=5, sticky="ew")
        
        # Speaker
        self.speaker_label = ctk.CTkLabel(self.device_frame, text="Speaker:")
        self.speaker_label.grid(row=0, column=2, padx=(20, 10), pady=5, sticky="w")
        self.speaker_dropdown = ttk.Combobox(self.device_frame, values=output_devices, state="readonly", width=30)
        if output_devices and output_devices[0] != "None":
            self.speaker_dropdown.set(output_devices[0])
        self.speaker_dropdown.grid(row=0, column=3, pady=5, sticky="ew")

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
            pass
            
        # --- 2. Main Tabs ---
        self.main_tabs = ctk.CTkTabview(self)
        self.main_tabs.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="nsew")
        
        self.tab_receive = self.main_tabs.add("Receive")
        self.tab_send = self.main_tabs.add("Send")
        
        self.setup_receive_tab()
        self.setup_send_tab()
        
    # ==========================================
    # RECEIVER LOGIC
    # ==========================================
    def setup_receive_tab(self):
        self.tab_receive.grid_columnconfigure(0, weight=1)
        self.tab_receive.grid_rowconfigure(3, weight=1)
        
        # Buttons
        self.button_frame = ctk.CTkFrame(self.tab_receive, fg_color="transparent")
        self.button_frame.grid(row=0, column=0, pady=10, sticky="ew")
        self.button_frame.grid_columnconfigure(0, weight=1)
        self.button_frame.grid_columnconfigure(1, weight=1)

        self.start_button = ctk.CTkButton(
            self.button_frame, text="Start Listening", command=self.start_listening_thread,
            font=("Arial", 16, "bold"), height=45
        )
        self.start_button.grid(row=0, column=0, padx=(0, 10), sticky="ew")

        self.stop_button = ctk.CTkButton(
            self.button_frame, text="Stop", command=self.stop_listening,
            font=("Arial", 16, "bold"), height=45, fg_color="#dc3545", hover_color="#c82333"
        )
        self.stop_button.grid(row=0, column=1, padx=(10, 0), sticky="ew")
        
        # Mic Level
        self.mic_level_frame = ctk.CTkFrame(self.tab_receive, fg_color="transparent")
        self.mic_level_frame.grid(row=1, column=0, pady=(0, 10), sticky="ew")
        self.mic_level_frame.grid_columnconfigure(0, weight=1)
        
        self.mic_level_bar = ctk.CTkProgressBar(self.mic_level_frame, height=10, fg_color="#333333", progress_color="#00ff00")
        self.mic_level_bar.grid(row=0, column=0, sticky="ew")
        self.mic_level_bar.set(0.0)
        
        # Progress Bar
        self.progress_frame = ctk.CTkFrame(self.tab_receive, fg_color="transparent")
        self.progress_frame.grid(row=2, column=0, pady=(0, 10), sticky="ew")
        self.progress_frame.grid_columnconfigure(0, weight=1)
        
        self.progress_bar = ctk.CTkProgressBar(self.progress_frame, height=20)
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.progress_bar.set(0.0)
        
        self.progress_label = ctk.CTkLabel(self.progress_frame, text="0%", width=40)
        self.progress_label.grid(row=0, column=1, padx=(10, 0))
        
        # Console
        self.console = ctk.CTkTextbox(
            self.tab_receive, font=("Consolas", 14), wrap="word", state="disabled",
            fg_color="#1e1e1e", text_color="#00ff00"
        )
        self.console.grid(row=3, column=0, pady=10, sticky="nsew")
        
        # Threading and State
        self.log_queue = queue.Queue()
        self.original_stdout = sys.stdout
        sys.stdout = QueueStdout(self.log_queue)
        
        self.node = None
        self.is_running = False
        self.received_chunks = {}
        self.total_chunks = 0
        
        print("[System] Application started. Ready to listen for acoustic data.")
        self.process_log_queue()

    def process_log_queue(self):
        while not self.log_queue.empty():
            try:
                msg = self.log_queue.get_nowait()
                self.log_to_console(msg)
            except queue.Empty:
                break
        self.after(50, self.process_log_queue)
        
    def log_to_console(self, text):
        if text.strip() == "CALIBRATING":
            self.start_button.configure(text="Calibrating...", fg_color="#ffcc00")
            return
        if text.strip() == "CALIBRATION_COMPLETE":
            self.start_button.configure(text="Listening for data...", fg_color="#28a745")
            return
            
        droplet_match = re.search(r'DROPLET (\d+)/(\d+)', text)
        if droplet_match:
            collected = int(droplet_match.group(1))
            required = int(droplet_match.group(2))
            pct = collected / required if required > 0 else 0
            self.progress_bar.set(pct)
            self.progress_label.configure(text=f"{int(pct * 100)}%")
            return
        
        start_match = re.search(r'\[START:(\d+):(\d+)\]', text)
        if start_match:
            total_chunks = start_match.group(1)
            file_size = start_match.group(2)
            self.total_chunks = int(total_chunks)
            self.received_chunks.clear()
            self.progress_bar.set(0.0)
            self.progress_label.configure(text="0%")
            text = f"\n>>> INCOMING FILE: {file_size} bytes ({total_chunks} chunks) <<<\n{text}"
            
        chunk_data_match = re.search(r'CHUNK (\d+)/(\d+): (.*)', text)
        if chunk_data_match:
            current = int(chunk_data_match.group(1))
            total = int(chunk_data_match.group(2))
            data = chunk_data_match.group(3).strip()
            
            self.total_chunks = total
            self.received_chunks[current] = data
            if len(self.received_chunks) == total:
                self.after(10, self.save_file)
                
        self.console.configure(state="normal")
        self.console.insert("end", text)
        self.console.see("end")
        self.console.configure(state="disabled")

    def save_file(self):
        try:
            sorted_keys = sorted(self.received_chunks.keys())
            b64_str = "".join([self.received_chunks[k] for k in sorted_keys])
            file_data = base64.b64decode(b64_str)
            timestamp = int(time.time())
            filename = f"received_file_{timestamp}.bin"
            
            with open(filename, "wb") as f:
                f.write(file_data)
                
            self.console.configure(state="normal")
            self.console.insert("end", f"\n\n[SUCCESS] File fully reconstructed and saved as '{filename}'!\n")
            self.console.see("end")
            self.console.configure(state="disabled")
            
            self.received_chunks.clear()
            self.total_chunks = 0
        except Exception as e:
            self.console.configure(state="normal")
            self.console.insert("end", f"\n\n[ERROR] Failed to save reconstructed file: {e}\n")
            self.console.see("end")
            self.console.configure(state="disabled")

    def update_mic_level(self, rms):
        normalized = min(rms / 10000.0, 1.0)
        if math.isnan(normalized) or math.isinf(normalized):
            normalized = 0.0
        self.mic_level_bar.set(normalized)

    def transceiver_worker(self, mic_id, speaker_id):
        try:
            self.node = AcousticNode(input_device=mic_id, output_device=speaker_id)
            def on_receive(text):
                print(text)
            def on_volume_update(rms):
                self.after(0, self.update_mic_level, rms)
                
            self.node.listen(callback=on_receive, on_volume_update=on_volume_update)
            while self.is_running:
                time.sleep(0.5)
        except Exception as e:
            print(f"[Error] Transceiver failed: {e}")
        finally:
            if self.node:
                self.node.stop_listening()

    def stop_listening(self):
        if not self.is_running:
            return
        self.is_running = False
        if self.node:
            self.node.stop_listening()
        self.start_button.configure(state="normal", text="Start Listening")
        
    def start_listening_thread(self):
        if self.is_running:
            return
            
        mic_selection = self.mic_dropdown.get()
        speaker_selection = self.speaker_dropdown.get()
        
        try:
            mic_id = int(re.search(r'\[(\d+)\]', mic_selection).group(1))
            speaker_id = int(re.search(r'\[(\d+)\]', speaker_selection).group(1))
            
            settings = {}
            if os.path.exists("settings.json"):
                with open("settings.json", "r") as f:
                    try:
                        settings = json.load(f)
                    except: pass
            settings["mic"] = mic_selection
            settings["speaker"] = speaker_selection
            with open("settings.json", "w") as f:
                json.dump(settings, f)
        except Exception:
            print("[Error] Invalid device selected.")
            return
            
        self.start_button.configure(state="disabled")
        self.progress_bar.set(0.0)
        self.progress_label.configure(text="0%")
        self.is_running = True
        
        threading.Thread(target=self.transceiver_worker, args=(mic_id, speaker_id), daemon=True).start()

    # ==========================================
    # SENDER LOGIC
    # ==========================================
    def setup_send_tab(self):
        self.tab_send.grid_columnconfigure(0, weight=1)
        self.tab_send.grid_rowconfigure(0, weight=1)
        
        self.send_tabview = ctk.CTkTabview(self.tab_send)
        self.send_tabview.grid(row=0, column=0, pady=(0, 10), sticky="nsew")
        
        self.tab_text = self.send_tabview.add("Send Text")
        self.tab_file = self.send_tabview.add("Send File")
        
        # Text
        self.textbox = ctk.CTkTextbox(self.tab_text, font=("Consolas", 14), wrap="word")
        self.textbox.pack(fill="both", expand=True, padx=10, pady=10)
        self.textbox.insert("0.0", "Type a message to send acoustically...")
        
        # File
        self.file_label = ctk.CTkLabel(self.tab_file, text="No file selected", font=("Arial", 14))
        self.file_label.pack(pady=(40, 20))
        
        self.select_file_btn = ctk.CTkButton(self.tab_file, text="Select File", command=self.select_file)
        self.select_file_btn.pack()
        self.selected_filepath = None
        
        # Send Section
        self.status_label = ctk.CTkLabel(self.tab_send, text="Ready", text_color="#aaaaaa")
        self.status_label.grid(row=1, column=0, pady=(0, 5))
        
        self.send_button = ctk.CTkButton(
            self.tab_send, text="Encode & Transmit", command=self.start_transmission,
            font=("Arial", 16, "bold"), height=45
        )
        self.send_button.grid(row=2, column=0, pady=(0, 10), sticky="ew")
        
        self.is_sending = False
        
    def select_file(self):
        filepath = filedialog.askopenfilename()
        if filepath:
            self.selected_filepath = filepath
            self.file_label.configure(text=f"Selected: {os.path.basename(filepath)}")

    def start_transmission(self):
        if self.is_sending:
            return
            
        speaker_selection = self.speaker_dropdown.get()
        try:
            speaker_id = int(re.search(r'\[(\d+)\]', speaker_selection).group(1))
            
            settings = {}
            if os.path.exists("settings.json"):
                with open("settings.json", "r") as f:
                    try:
                        settings = json.load(f)
                    except: pass
            settings["speaker"] = speaker_selection
            with open("settings.json", "w") as f:
                json.dump(settings, f)
        except Exception:
            self.status_label.configure(text="Invalid speaker selected", text_color="#ff4444")
            return

        active_tab = self.send_tabview.get()
        if active_tab == "Send Text":
            text_content = self.textbox.get("0.0", "end").strip()
            if not text_content:
                self.status_label.configure(text="Please enter some text", text_color="#ff4444")
                return
            payload_data = text_content.encode('utf-8')
        else:
            if not self.selected_filepath or not os.path.exists(self.selected_filepath):
                self.status_label.configure(text="Please select a valid file", text_color="#ff4444")
                return
            with open(self.selected_filepath, "rb") as f:
                payload_data = f.read()
                
        self.is_sending = True
        self.send_button.configure(state="disabled", text="Encoding...")
        self.status_label.configure(text="Encoding and compressing...", text_color="#aaaaaa")
        
        threading.Thread(target=self.transmission_worker, args=(payload_data, speaker_id), daemon=True).start()
        
    def transmission_worker(self, payload_data, speaker_id):
        try:
            waveform, rate = generate_wav(output_file=None, input_data=payload_data)
            duration = len(waveform) / rate
            
            self.after(0, lambda: self.status_label.configure(
                text=f"Transmitting... ({duration:.1f}s)", text_color="#00ff00"
            ))
            self.after(0, lambda: self.send_button.configure(text="Transmitting (Playing Audio)..."))
            
            sd.play(waveform, samplerate=rate, device=speaker_id, blocking=True)
            
            self.after(0, lambda: self.status_label.configure(text="Transmission complete!", text_color="#00ff00"))
        except Exception as e:
            traceback.print_exc()
            self.after(0, lambda: self.status_label.configure(text=f"Error: {e}", text_color="#ff4444"))
        finally:
            self.is_sending = False
            self.after(0, lambda: self.send_button.configure(state="normal", text="Encode & Transmit"))

    def destroy(self):
        sys.stdout = self.original_stdout
        self.is_running = False
        if self.node:
            self.node.stop_listening()
        super().destroy()

if __name__ == "__main__":
    app = SonicDropUI()
    app.mainloop()
