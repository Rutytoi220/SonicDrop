/* ═══════════════════════════════════════════════════════════
   SonicDrop — app.js
   Acoustic file receiver using ggwave.js + LT Fountain Codes
   ═══════════════════════════════════════════════════════════ */

// ── Deterministic LCG (must match Python sender exactly) ───
class LCG {
    constructor(seed) {
        this.state = (seed >>> 0) & 0xFFFFFFFF;
    }
    random() {
        this.state = (Math.imul(this.state, 1664525) + 1013904223) >>> 0;
        return this.state / 4294967296.0;
    }
    sample(pop_size, k) {
        const arr = [];
        for (let i = 0; i < pop_size; i++) arr.push(i);
        for (let i = pop_size - 1; i > pop_size - 1 - k; i--) {
            const j = Math.floor(this.random() * (i + 1));
            const tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;
        }
        return arr.slice(pop_size - k);
    }
}

// ── LT Fountain Decoder ───────────────────────────────────
class FountainDecoder {
    constructor(totalBlocks, originalSize, chunkSize = 90) {
        this.K = totalBlocks;
        this.originalSize = originalSize;
        this.chunkSize = chunkSize;
        this.pool = [];
        this.solved = new Map();
    }

    _soliton(rng) {
        let d = rng.random();
        if (d < 1 / this.K) return 1;
        const probs = [1 / this.K];
        for (let i = 2; i <= this.K; i++) probs.push(1 / (i * (i - 1)));
        const s = probs.reduce((a, b) => a + b, 0);
        let r = rng.random(), cum = 0;
        for (let i = 0; i < probs.length; i++) {
            cum += probs[i] / s;
            if (r <= cum) return i + 1;
        }
        return this.K;
    }

    _indices(seed) {
        const rng = new LCG(seed);
        const d = this._soliton(rng);
        return new Set(rng.sample(this.K, d));
    }

    addDroplet(seed, b64) {
        const raw = atob(b64);
        const payload = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) payload[i] = raw.charCodeAt(i);
        this.pool.push({ indices: this._indices(seed), payload });
        this._propagate();
    }

    _propagate() {
        let changed = true;
        while (changed) {
            changed = false;
            for (const drop of this.pool) {
                if (drop.indices.size !== 1) continue;
                const idx = drop.indices.values().next().value;
                if (this.solved.has(idx)) continue;
                this.solved.set(idx, drop.payload);
                changed = true;
                for (const other of this.pool) {
                    if (other === drop || !other.indices.has(idx)) continue;
                    other.indices.delete(idx);
                    const sp = this.solved.get(idx);
                    for (let i = 0; i < other.payload.length; i++) other.payload[i] ^= sp[i];
                }
            }
        }
    }

    get complete() { return this.solved.size === this.K; }

    reconstruct() {
        if (!this.complete) return null;
        const keys = [...this.solved.keys()].sort((a, b) => a - b);
        const buf = new Uint8Array(keys.length * this.chunkSize);
        let off = 0;
        for (const k of keys) { buf.set(this.solved.get(k), off); off += this.chunkSize; }
        let b64 = '';
        for (let i = 0; i < buf.length; i++) b64 += String.fromCharCode(buf[i]);
        b64 = b64.replace(/\0+$/, '');
        const bin = atob(b64);
        const out = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
        return out.slice(0, this.originalSize);
    }
}

// ── DOM refs ──────────────────────────────────────────────
const micSelect   = document.getElementById('micSelect');
const spkSelect   = document.getElementById('spkSelect');
const listenBtn   = document.getElementById('listenBtn');
const stopBtn     = document.getElementById('stopBtn');
const testBtn     = document.getElementById('testBtn');
const statusBadge = document.getElementById('statusIndicator');
const progressBar = document.getElementById('progressBar');
const progressTxt = document.getElementById('progressText');
const dropletTxt  = document.getElementById('dropletText');
const terminal    = document.getElementById('consoleOutput');
const levelBar    = document.getElementById('levelBar');

// ── State ─────────────────────────────────────────────────
let gwModule = null;     // ggwave WASM module
let gwInstance = null;   // ggwave instance handle (integer)
let audioCtx = null;
let mediaStream = null;
let processor = null;
let source = null;
let decoder = null;
let listening = false;
const seenSeeds = new Set();

// ── Logging ───────────────────────────────────────────────
function log(msg, cls = 'log-system') {
    const p = document.createElement('p');
    p.className = cls;
    p.textContent = `> ${msg}`;
    terminal.appendChild(p);
    terminal.scrollTop = terminal.scrollHeight;
}

// ── Device enumeration ────────────────────────────────────
async function enumerateDevices() {
    try {
        const tmpStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        tmpStream.getTracks().forEach(t => t.stop());
    } catch (_) { /* labels will be generic */ }

    const devices = await navigator.mediaDevices.enumerateDevices();
    micSelect.innerHTML = '';
    spkSelect.innerHTML = '';

    let micCount = 0, spkCount = 0;
    for (const d of devices) {
        if (d.kind === 'audioinput') {
            const opt = document.createElement('option');
            opt.value = d.deviceId;
            opt.textContent = d.label || `Microphone ${++micCount}`;
            micSelect.appendChild(opt);
        } else if (d.kind === 'audiooutput') {
            const opt = document.createElement('option');
            opt.value = d.deviceId;
            opt.textContent = d.label || `Speaker ${++spkCount}`;
            spkSelect.appendChild(opt);
        }
    }
    if (!micSelect.options.length) micSelect.innerHTML = '<option>No microphones found</option>';
    if (!spkSelect.options.length) spkSelect.innerHTML = '<option>Default</option>';

    log(`Found ${micCount} mic(s), ${spkCount} speaker(s).`, 'log-info');
}

// ── ggwave bootstrap ──────────────────────────────────────
ggwave_factory().then(mod => {
    gwModule = mod;
    const p = gwModule.getDefaultParameters();
    p.sampleRateInp = 48000;
    p.sampleRateOut = 48000;
    p.sampleRate    = 48000;
    // Keep default samplesPerFrame (1024) — do NOT override
    gwInstance = gwModule.init(p);

    log(`ggwave WASM loaded — instance=${gwInstance}, 48 kHz, F32 format.`, 'log-success');
    listenBtn.disabled = false;
    testBtn.disabled = false;

    enumerateDevices();
}).catch(err => {
    log(`FATAL: ggwave failed — ${err}`, 'log-error');
});

// ── Start listening ───────────────────────────────────────
listenBtn.addEventListener('click', async () => {
    if (listening || !gwModule) return;

    const deviceId = micSelect.value;

    try {
        log('Requesting microphone access…', 'log-info');
        mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                deviceId: deviceId ? { exact: deviceId } : undefined,
                echoCancellation: false,
                noiseSuppression: false,
                autoGainControl: false,
                sampleRate: { ideal: 48000 }
            }
        });

        audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
        source = audioCtx.createMediaStreamSource(mediaStream);

        // Use 4096-sample buffer (matches official ggwave examples)
        processor = audioCtx.createScriptProcessor(4096, 1, 1);
        processor.onaudioprocess = onAudioFrame;

        source.connect(processor);
        processor.connect(audioCtx.destination);

        listening = true;
        listenBtn.disabled = true;
        stopBtn.disabled = false;
        setStatus('Listening', 'listening');

        const track = mediaStream.getAudioTracks()[0];
        const settings = track.getSettings();
        log(`Mic: "${track.label}"`, 'log-info');
        log(`Stream: ${settings.sampleRate || audioCtx.sampleRate} Hz, ${settings.channelCount || 1}ch. Waiting for [START]…`, 'log-info');

    } catch (err) {
        log(`Mic error: ${err.message}`, 'log-error');
        setStatus('Error', 'error');
    }
});

// ── Stop listening ────────────────────────────────────────
stopBtn.addEventListener('click', stopListening);

function stopListening() {
    if (processor)   { processor.disconnect(); processor = null; }
    if (source)      { source.disconnect(); source = null; }
    if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
    // Don't close audioCtx — we reuse it for test tones
    listening = false;
    listenBtn.disabled = false;
    stopBtn.disabled = true;
    setStatus('Idle', 'idle');
    levelBar.style.width = '0%';
    log('Stopped.', 'log-system');
}

// ── Test loopback: encode "hello" and play it through speakers ──
testBtn.addEventListener('click', () => {
    if (!gwModule || gwInstance === null) return;

    log('Encoding test message "SonicDrop test" …', 'log-info');

    const testPayload = 'SonicDrop test';
    const waveform = gwModule.encode(
        gwInstance,
        testPayload,
        gwModule.ProtocolId.GGWAVE_PROTOCOL_AUDIBLE_FASTEST,
        10  // volume
    );

    if (!waveform || waveform.length === 0) {
        log('Encode returned empty waveform!', 'log-error');
        return;
    }

    // waveform is Int8Array over float32 bytes
    const f32 = new Float32Array(waveform.buffer, waveform.byteOffset, waveform.length / 4);
    log(`Encoded ${f32.length} samples (${(f32.length / 48000).toFixed(2)}s). Playing…`, 'log-info');

    const ctx = audioCtx || new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
    if (!audioCtx) audioCtx = ctx;

    const buf = ctx.createBuffer(1, f32.length, 48000);
    buf.getChannelData(0).set(f32);

    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    src.start();
    src.onended = () => log('Test tone finished playing.', 'log-info');
});

// ── Audio processing callback ─────────────────────────────
let frameCount = 0;

function onAudioFrame(e) {
    const f32 = e.inputBuffer.getChannelData(0);

    // Update mic level meter
    let sumSq = 0;
    for (let i = 0; i < f32.length; i++) sumSq += f32[i] * f32[i];
    const rms = Math.sqrt(sumSq / f32.length);
    const levelPct = Math.min(100, rms * 500); // scale for visibility
    levelBar.style.width = `${levelPct}%`;

    // Log first few frames so user can verify mic is alive
    frameCount++;
    if (frameCount <= 3) {
        log(`Frame ${frameCount}: ${f32.length} samples, RMS=${rms.toFixed(6)}, peak=${Math.max(...f32.slice(0, 100)).toFixed(4)}`, 'log-data');
    }

    // ggwave.js uses F32 sample format by default.
    // It expects raw float32 bytes as an Int8Array.
    // getChannelData returns a view into AudioBuffer's internal memory, so copy it.
    const copy = new Float32Array(f32);
    const res = gwModule.decode(gwInstance, new Int8Array(copy.buffer));

    if (res && res.length > 0) {
        const text = new TextDecoder().decode(res);
        log(`DECODED: "${text}"`, 'log-success');
        handlePayload(text);
    }
}

// ── Payload dispatcher ────────────────────────────────────
function handlePayload(text) {
    const sm = text.match(/^\[START:(\d+):(\d+)\]/);
    if (sm) {
        const K = parseInt(sm[1]), size = parseInt(sm[2]);
        decoder = new FountainDecoder(K, size, 90);
        seenSeeds.clear();
        updateProgress();
        log(`▸ INCOMING FILE — ${K} blocks, ${size} bytes`, 'log-info');
        return;
    }

    const dm = text.match(/^\[DROPLET:(\d+)\](.*)/);
    if (dm && decoder) {
        const seed = parseInt(dm[1]);
        if (seenSeeds.has(seed)) return;
        seenSeeds.add(seed);
        decoder.addDroplet(seed, dm[2]);
        updateProgress();
        log(`▸ Droplet seed=${seed}  rank ${decoder.solved.size}/${decoder.K}`, 'log-data');

        if (decoder.complete) {
            log('✓ Matrix fully ranked — reconstructing…', 'log-success');
            const data = decoder.reconstruct();
            if (data) {
                triggerDownload(data, `sonicdrop_${Date.now()}.bin`);
                log('✓ Download triggered!', 'log-success');
            } else {
                log('✗ Reconstruction failed.', 'log-error');
            }
            decoder = null;
        }
        return;
    }

    // Plain text (not a droplet or start header)
    // Already logged as DECODED above
}

// ── UI helpers ────────────────────────────────────────────
function setStatus(label, cls) {
    statusBadge.textContent = label;
    statusBadge.className = `status-badge ${cls}`;
}

function updateProgress() {
    if (!decoder) return;
    const required = Math.ceil(decoder.K * 1.15);
    const got = Math.min(decoder.pool.length, required);
    const pct = Math.floor((got / required) * 100);
    progressBar.style.width = `${pct}%`;
    progressTxt.textContent = `${pct} %`;
    dropletTxt.textContent  = `${got} / ${required} droplets`;
}

function triggerDownload(bytes, name) {
    const blob = new Blob([bytes], { type: 'application/octet-stream' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
}
