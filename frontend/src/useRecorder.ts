import { useCallback, useRef, useState } from "react";

/** Records the microphone and reports loudness through the --level CSS variable,
 *  which is what makes the core ring react while you speak. */
export function useRecorder() {
  const [recording, setRecording] = useState(false);
  const session = useRef<{ recorder: MediaRecorder; chunks: Blob[]; stream: MediaStream; ctx: AudioContext; raf: number } | null>(null);

  const start = useCallback(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((m) => MediaRecorder.isTypeSupported(m));
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);

    const ctx = new AudioContext();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);
    const tick = () => {
      analyser.getByteTimeDomainData(samples);
      let peak = 0;
      for (const s of samples) peak = Math.max(peak, Math.abs(s - 128) / 128);
      document.documentElement.style.setProperty("--level", Math.min(1, peak * 2.2).toFixed(3));
      if (session.current) session.current.raf = requestAnimationFrame(tick);
    };
    session.current = { recorder, chunks, stream, ctx, raf: requestAnimationFrame(tick) };
    recorder.start();
    setRecording(true);
  }, []);

  const stop = useCallback(
    () =>
      new Promise<Blob | null>((resolve) => {
        const s = session.current;
        if (!s) return resolve(null);
        s.recorder.onstop = () => {
          cancelAnimationFrame(s.raf);
          s.stream.getTracks().forEach((t) => t.stop());
          void s.ctx.close();
          session.current = null;
          document.documentElement.style.setProperty("--level", "0");
          setRecording(false);
          resolve(new Blob(s.chunks, { type: s.recorder.mimeType || "audio/webm" }));
        };
        s.recorder.stop();
      }),
    [],
  );

  return { recording, start, stop };
}
