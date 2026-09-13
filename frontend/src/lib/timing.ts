export function validDuration(value: unknown, maximum = 600): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 && value <= maximum;
}

export function formatDuration(value: unknown, maximum = 600): string {
  if (!validDuration(value, maximum)) return "—";
  const milliseconds = Math.round(value * 1000);
  const minutes = Math.floor(milliseconds / 60_000);
  const seconds = Math.floor((milliseconds % 60_000) / 1000);
  const remainder = milliseconds % 1000;
  return minutes ? `${minutes}:${String(seconds).padStart(2, "0")}.${String(remainder).padStart(3, "0")}` : `${seconds}.${String(remainder).padStart(3, "0")}`;
}

export function formatLapTime(value: unknown): string {
  return formatDuration(value, 300);
}

export function formatGap(value: unknown): string {
  if (typeof value === "string") return value.trim() || "—";
  const duration = formatDuration(value, 300);
  return duration === "—" ? duration : `+${duration}`;
}

export function formatPitStop(value: unknown): string {
  if (!validDuration(value, 120)) return "—";
  return `${value.toFixed(2)}s`;
}
