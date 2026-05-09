export function streamText(
  value: string,
  onChunk: (value: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve) => {
    let index = 0;
    const step = () => {
      if (signal?.aborted) {
        onChunk(value);
        resolve();
        return;
      }
      index = Math.min(value.length, index + Math.max(1, Math.ceil(value.length / 90)));
      onChunk(value.slice(0, index));
      if (index >= value.length) {
        resolve();
        return;
      }
      requestAnimationFrame(step);
    };
    step();
  });
}
