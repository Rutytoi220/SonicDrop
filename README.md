# SonicDrop 🎵

SonicDrop is a powerful, offline, self-contained acoustic file transfer application. It securely transmits text and files across an air-gap using only your computer's speaker and microphone via Frequency-Shift Keying (FSK).

## 🚀 Features

*   **Completely Air-Gapped**: Transfer files between devices without WiFi, Bluetooth, or any network connection.
*   **Data Carousel Architecture**: Files are compressed with Zlib, chunked, and transmitted in continuous cyclical passes. The receiver accumulates these "droplets" and reconstructs the file out-of-order, making it highly resilient to background noise or brief interruptions.
*   **Intelligent DSP & Noise Gating**: Uses dynamic noise floor calibration and zero-padding phase preservation. It perfectly handles severe room echo and reverberation without breaking `ggwave`'s Phase-Locked Loop (PLL).
*   **Unified Modern UI**: Built with `customtkinter`, featuring a beautiful dark-mode interface with live acoustic progress bars and console logging.
*   **Hardware Aware**: Automatically detects and recommends the best native hardware sample rates (e.g., 48kHz) to prevent OS-level resampling artifacts.

## 🛠️ Technology Stack

*   **Core DSP Backend**: [ggwave](https://github.com/ggerganov/ggwave) (C++ FSK Library)
*   **Audio I/O**: `sounddevice` / PortAudio
*   **Signal Processing**: `numpy`, `scipy`
*   **Compression**: `zlib`
*   **UI Framework**: `customtkinter`

## ⚙️ Installation & Usage

1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/Rutytoi220/SoundSonic.git
    cd SoundSonic
    ```

2.  **Run the Unified UI**:
    SonicDrop is built to be run with [uv](https://github.com/astral-sh/uv) (or standard Python).
    ```bash
    uv run python sonicdrop_ui.py
    ```

3.  **Sending a File**:
    *   Select your output speaker from the top dropdown (the system default is recommended).
    *   Switch to the **Send** tab.
    *   Type a text message or select a file to send.
    *   Click **Encode & Transmit**.

4.  **Receiving a File**:
    *   Select your input microphone.
    *   Switch to the **Receive** tab.
    *   Click **Start Listening**. The app will calibrate to your room's noise floor for 2 seconds.
    *   Play the transmission from the sender. Watch the droplets accumulate in the progress bar!
    *   Once fully received, the file is automatically decompressed and saved to your directory.

## 🧠 How it Works

When you send a file, SonicDrop:
1. Compresses the raw bytes using `zlib`.
2. Slices the data into 12-byte payloads, attaching a 4-byte index header (total 16-byte chunks).
3. Loops the entire sequence of chunks 4 times (Data Carousel) with 2.0-second Inter-Symbol Interference (ISI) silence gaps to allow room reverberation to decay.
4. Encodes each chunk into audio waveforms using `ggwave`'s "Fastest" protocol.

When receiving, SonicDrop:
1. Calibrates the room's noise floor.
2. Applies a strict dynamic noise gate. When the audio falls below the threshold (during silence gaps), it injects pure digital zeros instead of skipping frames. This preserves exact chronological phase alignment.
3. Decodes the FSK tones back into chunk indices and payloads.
4. Reconstructs the file once all unique chunks are collected.
