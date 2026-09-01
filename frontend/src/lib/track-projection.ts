// SPDX-License-Identifier: AGPL-3.0-only
//
// The one and only transform from OpenF1 circuit coordinates to SVG user space.
//
// Provider coordinates are a circuit-local Cartesian frame in tenths of a
// metre, with Y increasing away from the viewer the way a plot does. SVG's Y
// axis points down the screen, so Y is inverted exactly once, here. Scale is
// uniform on both axes so a circuit is never stretched to fill its container,
// and the result is centred inside the viewport with a fixed padding.
//
// Track geometry and driver markers both go through `projectPoint`. Nothing
// downstream is allowed to apply its own offset or scale: a per-circuit fudge
// would silently decouple the cars from the track.

export type TrackBounds = {
  min_x: number;
  max_x: number;
  min_y: number;
  max_y: number;
};

export type Viewport = {
  width: number;
  height: number;
  padding: number;
};

export type ProjectedPoint = { x: number; y: number };

export type TrackProjection = {
  viewport: Viewport;
  bounds: TrackBounds;
  scale: number;
  offsetX: number;
  offsetY: number;
};

export const DEFAULT_VIEWPORT: Viewport = { width: 1000, height: 1000, padding: 48 };

export function isUsableBounds(bounds: TrackBounds | null | undefined): bounds is TrackBounds {
  if (!bounds) return false;
  const values = [bounds.min_x, bounds.max_x, bounds.min_y, bounds.max_y];
  if (!values.every((value) => Number.isFinite(value))) return false;
  return bounds.max_x > bounds.min_x && bounds.max_y > bounds.min_y;
}

/**
 * Build a uniform, aspect-ratio preserving transform for a set of bounds.
 *
 * The smaller of the two axis ratios wins, so the circuit always fits and its
 * proportions survive; the leftover space becomes symmetric centring offsets.
 */
export function createTrackProjection(
  bounds: TrackBounds,
  viewport: Viewport = DEFAULT_VIEWPORT,
): TrackProjection {
  const usableWidth = Math.max(1, viewport.width - viewport.padding * 2);
  const usableHeight = Math.max(1, viewport.height - viewport.padding * 2);
  const spanX = Math.max(bounds.max_x - bounds.min_x, 1e-6);
  const spanY = Math.max(bounds.max_y - bounds.min_y, 1e-6);
  const scale = Math.min(usableWidth / spanX, usableHeight / spanY);
  return {
    viewport,
    bounds,
    scale,
    offsetX: viewport.padding + (usableWidth - spanX * scale) / 2,
    offsetY: viewport.padding + (usableHeight - spanY * scale) / 2,
  };
}

export function projectPoint(
  projection: TrackProjection,
  x: number,
  y: number,
): ProjectedPoint {
  return {
    x: projection.offsetX + (x - projection.bounds.min_x) * projection.scale,
    // Y inversion: provider Y grows "north", SVG Y grows down the screen.
    y: projection.offsetY + (projection.bounds.max_y - y) * projection.scale,
  };
}

export function projectPath(
  projection: TrackProjection,
  points: ReadonlyArray<readonly [number, number]>,
): string {
  if (points.length < 2) return "";
  return points
    .map((point, index) => {
      const projected = projectPoint(projection, point[0], point[1]);
      return `${index === 0 ? "M" : "L"}${projected.x.toFixed(2)},${projected.y.toFixed(2)}`;
    })
    .join(" ");
}

/** Fallback extent when a session has samples but no derived geometry yet. */
export function boundsFromPoints(
  points: ReadonlyArray<{ x: number; y: number }>,
): TrackBounds | null {
  if (!points.length) return null;
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const point of points) {
    if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
    minX = Math.min(minX, point.x);
    maxX = Math.max(maxX, point.x);
    minY = Math.min(minY, point.y);
    maxY = Math.max(maxY, point.y);
  }
  if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null;
  // A single car, or a field bunched on the grid, has a degenerate extent.
  // Widening it keeps the projection stable instead of dividing by ~zero.
  const spanX = maxX - minX;
  const spanY = maxY - minY;
  const pad = Math.max(spanX, spanY, 1000) * 0.1;
  return { min_x: minX - pad, max_x: maxX + pad, min_y: minY - pad, max_y: maxY + pad };
}
