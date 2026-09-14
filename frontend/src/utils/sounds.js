/**
 * Chess move sounds using the Web Audio API.
 * Simulates a wooden chess piece "tock" — tone + subtle noise layer, no files needed.
 */

function createCtx() {
  return new (window.AudioContext || window.webkitAudioContext)();
}

function playTock(frequency = 520, gainLevel = 0.35) {
  try {
    const ctx = createCtx();
    const t = ctx.currentTime;

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
export function playMoveSound() { playTock(500, 0.35); }

/** Opponent's move — slightly deeper tock. */
export function playOpponentMoveSound() { playTock(380, 0.28); }

/**
 * Clock tick — sharp high click, played every second when time < 20s.
 * Under 10s: slightly louder and higher for urgency.
 */
export function playTickSound(urgent = false) {
  try {
    const ctx = createCtx();
    const t = ctx.currentTime;
    const freq = urgent ? 1400 : 1100;
    const gain = urgent ? 0.28 : 0.18;

    const osc = ctx.createOscillator();
    osc.type = "square";
    osc.frequency.setValueAtTime(freq, t);

    const g = ctx.createGain();
    g.gain.setValueAtTime(gain, t);
    g.gain.exponentialRampToValueAtTime(0.001, t + 0.04);

    osc.connect(g);
    g.connect(ctx.destination);
    osc.start(t);
    osc.stop(t + 0.04);
  } catch {
    // Silently fail
  }
}
