// app.js
let audioContext = null;
let mediaStream = null;
let ggwaveModule = null;
let ggwaveInstance = null;
let audioProcessor = null;

const consoleDiv = document.getElementById('console');
const startBtn = document.getElementById('start-btn');
const sendBtn = document.getElementById('send-btn');
const textInput = document.getElementById('text-input');
const fileInput = document.getElementById('file-input');

function log(msg) {
    console.log(msg);
    consoleDiv.textContent += msg + '\n';
    consoleDiv.scrollTop = consoleDiv.scrollHeight;
}

// Chunk reassembly buffers
let receivedChunks = [];
let totalExpectedChunks = 0;

startBtn.addEventListener('click', () => {
    log("Initializing ggwave...");
    
    // The Emscripten Factory for the SINGLE_FILE=1 build
    ggwave_factory().then(async function(module) {
        ggwaveModule = module;
        const parameters = module.getDefaultParameters();
        ggwaveInstance = module.init(parameters); // Initialize the C++ instance
        
        try {
            // [CRITICAL] Initialize AudioContext STRICTLY inside the onClick handler to comply with Apple's Autoplay policy
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            audioContext = new AudioContext({ sampleRate: 48000 });
            
            log(`AudioContext created (Sample Rate: ${audioContext.sampleRate}Hz)`);
            
            // [CRITICAL] Hardware Constraints for iOS
            const constraints = {
                audio: {
                    echoCancellation: false,
                    noiseSuppression: false,
                    autoGainControl: false
                }
            };
            
            mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
            log("Microphone access granted (Hardware filters explicitly disabled).");
            
            const source = audioContext.createMediaStreamSource(mediaStream);
            
            // ScriptProcessorNode for parsing audio chunks
            audioProcessor = audioContext.createScriptProcessor(1024, 1, 1);
            
            audioProcessor.onaudioprocess = function(e) {
                if (!ggwaveInstance || !ggwaveModule) return;
                const inputData = e.inputBuffer.getChannelData(0);
                
                // ggwave requires Int8Array byte stream representation of Int16 samples
                const pcm16 = new Int16Array(inputData.length);
                for (let i = 0; i < inputData.length; i++) {
                    pcm16[i] = Math.max(-32768, Math.min(32767, Math.floor(inputData[i] * 32768)));
                }
                
                const pcm8 = new Int8Array(pcm16.buffer);
                
                // Decode acoustic payload using the module object
                const res = ggwaveModule.decode(ggwaveInstance, pcm8);
                
                if (res && res.length > 0) {
                    let text = "";
                    if (typeof res === 'string') {
                        text = res;
                    } else {
                        text = new TextDecoder("utf-8").decode(res);
                    }
                    handleIncomingData(text);
                }
            };
            
            source.connect(audioProcessor);
            // Connect processor to destination (required for Safari to fire onaudioprocess events)
            audioProcessor.connect(audioContext.destination);
            
            log("Listening for incoming acoustic data...");
            startBtn.disabled = true;
            startBtn.textContent = "Audio Active";
            startBtn.style.background = "#28a745";
            sendBtn.disabled = false;
            
        } catch (err) {
            log("Error initializing audio context: " + err);
        }
    }).catch(function(err) {
        log("WASM Load Error: " + err);
        console.error("WASM Load Error:", err);
    });
});

function handleIncomingData(text) {
    // Regex to match [X/Y]header chunks
    const regex = /^\[(\d+)\/(\d+)\](.*)/;
    const match = text.match(regex);
    
    if (match) {
        const currentChunk = parseInt(match[1]);
        const totalChunks = parseInt(match[2]);
        const payloadData = match[3];
        
        log(`[Received File Chunk ${currentChunk}/${totalChunks}]`);
        
        // Reset if starting a new file
        if (currentChunk === 1) {
            receivedChunks = [];
            totalExpectedChunks = totalChunks;
        }
        
        // Store chunk in array (0-indexed)
        receivedChunks[currentChunk - 1] = payloadData;
        
        // Check if file is completely reassembled
        let receivedCount = receivedChunks.filter(c => c !== undefined).length;
        if (receivedCount === totalExpectedChunks && totalExpectedChunks > 0) {
            log("All chunks received! Reassembling file...");
            reassembleFile();
        }
    } else {
        log("Received string: " + text);
    }
}

function reassembleFile() {
    try {
        const base64Data = receivedChunks.join("");
        log(`Base64 concatenated length: ${base64Data.length}`);
        
        // Decode base64 to binary
        const binaryString = atob(base64Data);
        const len = binaryString.length;
        const bytes = new Uint8Array(len);
        for (let i = 0; i < len; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }
        
        const blob = new Blob([bytes], {type: "application/octet-stream"});
        const url = URL.createObjectURL(blob);
        
        // Trigger automatic file download
        const a = document.createElement("a");
        a.style.display = "none";
        a.href = url;
        a.download = "received_file_ggwave";
        document.body.appendChild(a);
        a.click();
        
        log("File download triggered on device!");
        
        setTimeout(() => {
            URL.revokeObjectURL(url);
            document.body.removeChild(a);
        }, 500);
        
        // Reset state for next file
        receivedChunks = [];
        totalExpectedChunks = 0;
        
    } catch (err) {
        log("Error during file reassembly: " + err);
    }
}

// Simple Send Implementation (Optional for client-side sending)
sendBtn.addEventListener('click', () => {
    if (!ggwaveInstance || !ggwaveModule) { log("Please start audio first!"); return; }
    const text = textInput.value;
    if (text) {
        log(`Sending text: ${text}`);
        // Encode as GGWAVE_PROTOCOL_AUDIBLE_FAST (1)
        const waveformData = ggwaveModule.encode(text, 1, 10, ggwaveInstance, 1);
        if (waveformData) {
            // Play out via AudioBufferSourceNode
            const audioBuffer = audioContext.createBuffer(1, waveformData.length, audioContext.sampleRate);
            audioBuffer.getChannelData(0).set(waveformData);
            const source = audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(audioContext.destination);
            source.start();
            log("Data sent.");
        }
    }
});
