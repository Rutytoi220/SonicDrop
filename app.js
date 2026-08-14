// app.js
let audioContext = null;
let mediaStream = null;
let ggwaveModule = null;
let ggwaveInstance = null;
let audioProcessor = null;

let isListening = false;
let audioSource = null;

const consoleDiv = document.getElementById('console');
const startBtn = document.getElementById('start-btn');
const sendBtn = document.getElementById('send-btn');
const textInput = document.getElementById('text-input');
const fileInput = document.getElementById('file-input');

const micSelect = document.getElementById("mic-select");
const speakerSelect = document.getElementById("speaker-select");
const recvStatus = document.getElementById("recv-status");
const recvProgress = document.getElementById("recv-progress");
const sendStatus = document.getElementById("send-status");

// Enumerate devices on load
async function populateDevices() {
    try {
        await navigator.mediaDevices.getUserMedia({ audio: true }); // Request permission first
        const devices = await navigator.mediaDevices.enumerateDevices();
        
        micSelect.innerHTML = "<option value=''>Default Microphone</option>";
        speakerSelect.innerHTML = "<option value=''>Default Speaker</option>";
        
        devices.forEach(device => {
            if (device.kind === "audioinput") {
                const opt = document.createElement("option");
                opt.value = device.deviceId;
                opt.text = device.label || `Microphone ${micSelect.length}`;
                micSelect.appendChild(opt);
            } else if (device.kind === "audiooutput") {
                const opt = document.createElement("option");
                opt.value = device.deviceId;
                opt.text = device.label || `Speaker ${speakerSelect.length}`;
                speakerSelect.appendChild(opt);
            }
        });
    } catch (err) {
        log("Failed to enumerate devices: " + err);
    }
}
populateDevices();


function log(msg) {
    console.log(msg);
    consoleDiv.textContent += msg + '\n';
    consoleDiv.scrollTop = consoleDiv.scrollHeight;
}

// Chunk reassembly buffers
let receivedChunks = [];
        if (recvStatus) recvStatus.textContent = "Status: Idle";
        if (recvProgress) recvProgress.style.width = "0%";
let totalExpectedChunks = 0;

startBtn.addEventListener('click', async () => {
    if (isListening) {
        // Stop listening
        log("Stopping microphone...");
        if (mediaStream) {
            mediaStream.getTracks().forEach(track => track.stop());
        }
        if (audioSource) {
            audioSource.disconnect();
            audioSource = null;
        }
        if (audioProcessor) {
            audioProcessor.disconnect();
            // Don't destroy audio@rocessor completely, just disconnect it from the graph
        }
        
        isListening = false;
        startBtn.textContent = "Start Microphone";
        startBtn.style.background = ""; // Restore default CSS
        startBtn.classList.remove("pulse-active");
        log("Audio stopped.");
        return;
    }

    log("Initializing ggwave...");
    
    if (!ggwaveModule) {
        // The Emscripten Factory for the SINGLE_FILE=1 build
        try {
            ggwaveModule = await ggwave_factory();
            const parameters = ggwaveModule.getDefaultParameters();
            parameters.sampleFormatInp = 4; // GGWAVE_SAMPLE_FORMAT_I16
            parameters.sampleRateInp = 48000;
            ggwaveInstance = ggwaveModule.init(parameters); // Initialize the C++ instance
        } catch (err) {
            log("WASM Load Error: " + err);
            console.error("WASM Load Error:", err);
            return;
        }
    }
        
    try {
        // [CRITICAL] Initialize AudioContext STRICTLY inside the onClick handler to comply with Apple's Autoplay policy
        if (!audioContext) {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            audioContext = new AudioContext({ sampleRate: 48000 });
            log(`AudioContext created (Sample Rate: ${audioContext.sampleRate}Hz)`);
        }
        
        // [CRITICAL] Hardware Constraints for iOS
        const constraints = {
            audio: {
                echoCancellation: false,
                noiseSuppression: false,
                autoGainControl: false
            }
        };
        if (micSelect && micSelect.value) {
            constraints.audio.deviceId = { exact: micSelect.value };
        }
        
        mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
        log("Microphone access granted (Hardware filters explicitly disabled).");
        
        audioSource = audioContext.createMediaStreamSource(mediaStream);
        
        // ScriptProcessorNode for parsing audio chunks
        if (!audioProcessor) {
            audioProcessor = audioContext.createScriptProcessor(1024, 1, 1);
            
            let frameCounter = 0;
            audioProcessor.onaudioprocess = function(e) {
                if (ggwaveInstance === null || ggwaveModule === null || !isListening) return;
                const inputData = e.inputBuffer.getChannelData(0);
                
                let maxVol = 0;
                for(let i=0; i<inputData.length; i++) maxVol = Math.max(maxVol, Math.abs(inputData[i]));
                
                frameCounter++;
                if (frameCounter >= 20) {
                    console.log("Mic Vol: " + maxVol.toFixed(4));
                    frameCounter = 0;
                }
                
                const micLevelBar = document.getElementById("mic-level-bar");
                if (micLevelBar) {
                    let pct = Math.min(maxVol * 300, 100);
                    micLevelBar.style.width = pct + "%";
                }
                
                const pcm16 = new Int16Array(inputData.length);
                for (let i = 0; i < inputData.length; i++) {
                    pcm16[i] = Math.max(-32768, Math.min(32767, Math.floor(inputData[i] * 32768)));
                }
                const res = ggwaveModule.decode(ggwaveInstance, new Int8Array(pcm16.buffer));
                
                if (res && res.length > 0) {
                    console.log(">>> DECODER CAUGHT DATA! Type:", typeof res, "| Length:", res.length, "| Data:", res);
                    let byteArray;
                    if (typeof res === "string") {
                        byteArray = new Uint8Array(res.length);
                        for (let j = 0; j < res.length; j++) byteArray[j] = res.charCodeAt(j);
                    } else {
                        byteArray = new Uint8Array(res);
                    }
                    handleIncomingData(byteArray);
                }
            };
        }
        
        audioSource.connect(audioProcessor);
        // Connect processor to destination (required for Safari to fire onaudioprocess events)
        audioProcessor.connect(audioContext.destination);
        
        if (audioContext.state === "suspended") {
            await audioContext.resume();
        }
        log("AudioContext state: " + audioContext.state);
        
        isListening = true;
        log("Listening for incoming acoustic data...");
        startBtn.textContent = "Stop Microphone";
        startBtn.style.background = "#ef4444"; // Red color for stop
        startBtn.classList.add("pulse-active");
        sendBtn.disabled = false;
        
    } catch (err) {
        log("Error initializing audio context: " + err);
    }
});function handleIncomingData(byteArray) {
    if (byteArray.length < 4) return;
    
    // Read 4-byte struct: <HH (chunk_idx, total_chunks)
    const dv = new DataView(byteArray.buffer, byteArray.byteOffset, byteArray.byteLength);
    const chunkIdx = dv.getUint16(0, true);
    const totalChunks = dv.getUint16(2, true);
    const payloadData = byteArray.slice(4);
    
    if (totalChunks <= 0 || chunkIdx >= totalChunks) return;
    
    log(`[Received File Chunk ${chunkIdx+1}/${totalChunks}]`);
    
    if (totalExpectedChunks !== totalChunks) {
        receivedChunks = [];
        if (recvStatus) recvStatus.textContent = "Status: Idle";
        if (recvProgress) recvProgress.style.width = "0%";
        totalExpectedChunks = totalChunks;
    }
    
    receivedChunks[chunkIdx] = payloadData;
    
    let receivedCount = receivedChunks.filter(c => c !== undefined).length;
    
    if (recvStatus) recvStatus.textContent = `Status: Receiving chunk ${receivedCount}/${totalChunks}`;
    if (recvProgress) recvProgress.style.width = `${(receivedCount / totalExpectedChunks) * 100}%`;
    
    if (receivedCount === totalExpectedChunks && totalExpectedChunks > 0) {
        log("All chunks received! Reassembling file...");
        if (recvStatus) recvStatus.textContent = "Status: Reassembling File...";
        reassembleFile();
    }
}

async function reassembleFile() {
    try {
        let totalLen = 0;
        for (let c of receivedChunks) totalLen += c.length;
        const compressedBytes = new Uint8Array(totalLen);
        let offset = 0;
        for (let c of receivedChunks) {
            compressedBytes.set(c, offset);
            offset += c.length;
        }
        
        log(`Compressed payload length: ${compressedBytes.length}`);
        
        // Decompress using Web DecompressionStream (zlib/deflate)
        const ds = new DecompressionStream("deflate");
        const rs = new Response(compressedBytes);
        const decompressedStream = rs.body.pipeThrough(ds);
        const decompressedResponse = new Response(decompressedStream);
        const decompressedBlob = await decompressedResponse.blob();
        
        log(`Decompressed length: ${decompressedBlob.size}`);
        
        const url = URL.createObjectURL(decompressedBlob);
        const a = document.createElement("a");
        a.style.display = "none";
        a.href = url;
        a.download = "received_file_ggwave.bin";
        document.body.appendChild(a);
        a.click();
        
        log("File download triggered on device!");
        
        setTimeout(() => {
            URL.revokeObjectURL(url);
            document.body.removeChild(a);
        }, 500);
        
        receivedChunks = [];
        if (recvStatus) recvStatus.textContent = "Status: Idle";
        if (recvProgress) recvProgress.style.width = "0%";
        totalExpectedChunks = 0;
        
    } catch (err) {
        log("Error during file reassembly: " + err);
    }
}

// Simple Send Implementation (Optional for client-side sending)
sendBtn.addEventListener('click', async () => {
    if (ggwaveInstance === null || ggwaveModule === null) { 
        log("Please start audio first!"); 
        if (sendStatus) sendStatus.textContent = "Error: Start Microphone First";
        return; 
    }
    const text = textInput.value;
    if (text) {
        log(`Sending text: ${text}`);
        if (sendStatus) sendStatus.textContent = "Status: Encoding data...";
        
        // Encode as GGWAVE_PROTOCOL_AUDIBLE_FAST (1)
        const waveformData = ggwaveModule.encode(text, 1, 10, ggwaveInstance, 1);
        if (waveformData) {
            if (sendStatus) sendStatus.textContent = "Status: Transmitting via Speaker...";
            
            // Handle specific speaker routing
            if (speakerSelect && speakerSelect.value && audioContext.setSinkId) {
                try {
                    await audioContext.setSinkId(speakerSelect.value);
                } catch (err) {
                    log("setSinkId failed: " + err);
                }
            }
            
            // Play out via AudioBufferSourceNode
            const audioBuffer = audioContext.createBuffer(1, waveformData.length, audioContext.sampleRate);
            audioBuffer.getChannelData(0).set(waveformData);
            const source = audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(audioContext.destination);
            source.start();
            
            // Revert status after duration
            const duration = (waveformData.length / audioContext.sampleRate) * 1000;
            setTimeout(() => {
                log("Data sent.");
                if (sendStatus) sendStatus.textContent = "Status: Idle";
            }, duration);
        }
    } else {
        if (sendStatus) sendStatus.textContent = "Error: No text to send";
    }
});
