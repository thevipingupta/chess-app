/**
 * Chess move sounds using the Web Audio API.
 * Simulates a wooden chess piece "tock" — tone + subtle noise layer, no files needed.
 */

function createCtx() {
  return new (window.AudioContext || window.webkitAudioContext)();
}

/**
 * Woody "tock" — a short sine tone at ~520 Hz with quick exponential decay,
 * layered with a thin noise transient for the initial attack click.
 */
function playTock(frequency = 520, gainLevel = 0.35) {
  try {
    const ctx = createCtx();
    const t = ctx.currentTime;

    /* ── Tone body (sine) ── */
    const osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.setValueAtTime(frequency, t);
    osc.frequency.exponentialRampToValueAtTime(frequency * 0.6, t + 0.08);

    const oscGain = ctx.createGain();
    oscGain.gain.setValueAtTime(gainLevel, t);
    oscGain.gain.exponentialRampToValueAtTime(0.001, t + 0.18);

    osc.connect(oscGain);
    oscGain.connect(ctx.destination);
    osc.start(t);
    osc.stop(t + 0.18);

    /* ── Attack transient (noise burst, very short) ── */
    const sampleRate = ctx.sampleRate;
    const bufSize = Math.floor(sampleRate * 0.025);
    const buf = ctx.createBuffer(1, bufSize, sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < bufSize; i++) {
      data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / bufSize, 4);
    }

    const noiseFilter = ctx.createBiquadFilter();
    noiseFilter.type = "bandpass";
    noiseFilter.frequency.value = frequency * 1.5;
    noiseFilter.Q.value = 0.8;

    const noiseGain = ctx.createGain();
    noiseGain.gain.setValueAtTime(gainLevel * 0.4, t);

    const noiseSource = ctx.createBufferSource();
    noiseSource.buffer = buf;
    noiseSource.connect(noiseFilter);
    noiseFilter.connect(noiseGain);
    noiseGain.connect(ctx.destination);
    noiseSource.start(t);
  } catch {
    // Silently fail
  }
}

/** Your move — warm, mid-range tock. */
export function playMoveSound() {
  playTock(500, 0.35);
}

/** Opponent's move — slightly deeper tock so you can tell them apart. */
export function playOpponentMoveSound() {
  playTock(380, 0.28);
}
