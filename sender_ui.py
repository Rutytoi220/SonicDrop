import customtkinter as ctk
from tkinter import ttk, filedialog
import threading
import sounddevice as sd
import traceback
import json
import os
import re

from generate_audio import generate_wav

class SenderUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("Acoustic Data Sender")
        self.geometry("650x550")
        
        # Set dark mode constraint
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        # 1. Device Selector
        self.device_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.device_frame.pack(fill="x", padx=20, pady=20)
        
        try:
            devices = sd.query_devices()
            output_devices = [f"[{i}] {d['name']}" for i, d in enumerate(devices) if d['max_output_channels'] > 0]
        except Exception as e:
            output_devices = ["None"]
            print(f"[Error] Failed to query audio devices: {e}")
            
        if not output_devices: output_devices = ["None"]
        
        self.speaker_label = ctk.CTkLabel(self.device_frame, text="Output Speaker:")
        self.speaker_label.pack(side="left", padx=(0, 10))
        
        self.speaker_dropdown = ttk.Combobox(self.device_frame, values=output_devices, state="readonly", width=50)
        if output_devices and output_devices[0] != "None":
            self.speaker_dropdown.set(output_devices[0])
        self.speaker_dropdown.pack(side="left", fill="x", expand=True)

        # Load saved settings
        try:
            if os.path.exists("settings.json"):
                with open("settings.json", "r") as f:
                    settings = json.load(f)
                    saved_speaker = settings.get("speaker")
                    if saved_speaker in output_devices:
                        self.speaker_dropdown.set(saved_speaker)
        except Exception as e:
            pass
            
        # 2. Input Method Tabs
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        
        self.tab_text = self.tabview.add("Send Text")
        self.tab_file = self.tabview.add("Send File")
        
        # Text Tab
        self.textbox = ctk.CTkTextbox(self.tab_text, font=("Consolas", 14), wrap="word")
        self.textbox.pack(fill="both", expand=True, padx=10, pady=10)
        self.textbox.insert("0.0", "Type a message to send acoustically...")
        
        # File Tab
        self.file_label = ctk.CTkLabel(self.tab_file, text="No file selected", font=("Arial", 14))
        self.file_label.pack(pady=(40, 20))
        
        self.select_file_btn = ctk.CTkButton(self.tab_file, text="Select File", command=self.select_file)
        self.select_file_btn.pack()
        self.selected_filepath = None
        
        # 3. Status and Send Button
        self.status_label = ctk.CTkLabel(self, text="Ready", text_color="#aaaaaa")
        self.status_label.pack(pady=(0, 5))
        
        self.send_button = ctk.CTkButton(
            self, 
            text="Encode & Transmit", 
            command=self.start_transmission,
            font=("Arial", 16, "bold"),
            height=45
        )
        self.send_button.pack(fill="x", padx=20, pady=(0, 20))
        
        self.is_sending = False
        
    def select_file(self):
        filepath = filedialog.askopenfilename()
        if filepath:
            self.selected_filepath = filepath
            self.file_label.configure(text=f"Selected: {os.path.basename(filepath)}")

    def start_transmission(self):
        if self.is_sending:
            return
            
        # Save speaker selection
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
            try:
                speaker_id = int(re.search(r'\[(\d+)\]', speaker_selection).group(1))
            except:
                self.status_label.configure(text="Invalid speaker selected", text_color="#ff4444")
                return

        # Get payload data
        active_tab = self.tabview.get()
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
        
        # Run encoding and playback in background thread
        threading.Thread(target=self.transmission_worker, args=(payload_data, speaker_id), daemon=True).start()
        
    def transmission_worker(self, payload_data, speaker_id):
        try:
            # 1. Generate Audio
            waveform, rate = generate_wav(output_file=None, input_data=payload_data)
            duration = len(waveform) / rate
            
            self.after(0, lambda: self.status_label.configure(
                text=f"Transmitting... ({duration:.1f}s)", text_color="#00ff00"
            ))
            self.after(0, lambda: self.send_button.configure(text="Transmitting (Playing Audio)..."))
            
            # 2. Play Audio
            sd.play(waveform, samplerate=rate, device=speaker_id, blocking=True)
            
            self.after(0, lambda: self.status_label.configure(text="Transmission complete!", text_color="#00ff00"))
        except Exception as e:
            traceback.print_exc()
            self.after(0, lambda: self.status_label.configure(text=f"Error: {e}", text_color="#ff4444"))
        finally:
            self.is_sending = False
            self.after(0, lambda: self.send_button.configure(state="normal", text="Encode & Transmit"))

if __name__ == "__main__":
    app = SenderUI()
    app.mainloop()
