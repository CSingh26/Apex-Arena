// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from "vitest";

import {
  boundsFromPoints,
  createTrackProjection,
  isUsableBounds,
  projectPath,
  projectPoint,
  type TrackBounds,
  type Viewport,
} from "@/lib/track-projection";

const viewport: Viewport = { width: 1000, height: 1000, padding: 50 };
const square: TrackBounds = { min_x: 0, max_x: 100, min_y: 0, max_y: 100 };

describe("createTrackProjection", () => {
  it("maps the bounds corners onto the padded viewport", () => {
    const projection = createTrackProjection(square, viewport);
    expect(projectPoint(projection, 0, 0)).toEqual({ x: 50, y: 950 });
    expect(projectPoint(projection, 100, 100)).toEqual({ x: 950, y: 50 });
  });

  it("inverts Y so provider 'up' renders as screen 'up'", () => {
    const projection = createTrackProjection(square, viewport);
    const low = projectPoint(projection, 50, 10);
    const high = projectPoint(projection, 50, 90);
    expect(high.y).toBeLessThan(low.y);
  });

  it("handles negative provider coordinates", () => {
    const projection = createTrackProjection(
      { min_x: -4330, max_x: 8311, min_y: -15762, max_y: 4537 },
      viewport,
    );
    const centre = projectPoint(projection, (-4330 + 8311) / 2, (-15762 + 4537) / 2);
    expect(centre.x).toBeCloseTo(500, 5);
    expect(centre.y).toBeCloseTo(500, 5);
  });

  it("preserves aspect ratio instead of stretching a tall circuit", () => {
    // 1:4 circuit in a square viewport: both axes must use the same scale.
    const projection = createTrackProjection(
      { min_x: 0, max_x: 100, min_y: 0, max_y: 400 },
      viewport,
    );
    const width = projectPoint(projection, 100, 0).x - projectPoint(projection, 0, 0).x;
    const height = projectPoint(projection, 0, 0).y - projectPoint(projection, 0, 400).y;
    expect(height / width).toBeCloseTo(4, 5);
    expect(height).toBeCloseTo(900, 5);
  });

  it("centres the circuit in the leftover space", () => {
    const projection = createTrackProjection(
      { min_x: 0, max_x: 100, min_y: 0, max_y: 400 },
      viewport,
    );
    const left = projectPoint(projection, 0, 0).x;
    const right = projectPoint(projection, 100, 0).x;
    expect(left - 0).toBeCloseTo(viewport.width - right, 5);
  });

  it("keeps track and markers on one transform", () => {
    const projection = createTrackProjection(square, viewport);
    const marker = projectPoint(projection, 25, 75);
    const path = projectPath(projection, [
      [25, 75],
      [75, 25],
    ]);
    expect(path.startsWith(`M${marker.x.toFixed(2)},${marker.y.toFixed(2)}`)).toBe(true);
  });

  it("scales with the viewport rather than assuming pixels", () => {
    const small = createTrackProjection(square, { width: 100, height: 100, padding: 5 });
    expect(projectPoint(small, 100, 0)).toEqual({ x: 95, y: 95 });
  });
});

describe("isUsableBounds", () => {
  it("rejects missing, degenerate and non-finite extents", () => {
    expect(isUsableBounds(null)).toBe(false);
    expect(isUsableBounds({ min_x: 0, max_x: 0, min_y: 0, max_y: 10 })).toBe(false);
    expect(isUsableBounds({ min_x: 0, max_x: NaN, min_y: 0, max_y: 10 })).toBe(false);
    expect(isUsableBounds(square)).toBe(true);
  });
});

describe("boundsFromPoints", () => {
  it("returns null without points", () => {
    expect(boundsFromPoints([])).toBeNull();
  });

  it("widens a degenerate extent so a single car still projects", () => {
    const bounds = boundsFromPoints([{ x: 500, y: -200 }]);
    expect(bounds).not.toBeNull();
    expect(isUsableBounds(bounds)).toBe(true);
  });

  it("ignores non-finite samples", () => {
    const bounds = boundsFromPoints([
      { x: 0, y: 0 },
      { x: NaN, y: 5 },
      { x: 100, y: 100 },
    ]);
    expect(bounds?.min_x).toBeLessThanOrEqual(0);
    expect(bounds?.max_x).toBeGreaterThanOrEqual(100);
  });
});
