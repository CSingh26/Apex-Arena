// SPDX-License-Identifier: AGPL-3.0-only
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const ROOM_SLUG = "day3-validation-room";
const API_BASE_URL = process.env.E2E_API_URL ?? "http://localhost:8764";
const VIEWPORT_WIDTHS = [1440, 1280, 1024, 768, 390] as const;

function collectBrowserErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

async function postRoomAction(request: APIRequestContext, path: string, data: object): Promise<void> {
  const response = await request.post(`${API_BASE_URL}/api/v1/race-rooms/${ROOM_SLUG}/${path}`, { data });
  expect(response.ok(), `${path} precondition returned HTTP ${response.status()}`).toBeTruthy();
}

async function openValidationRoom(page: Page): Promise<void> {
  await page.goto("/race-rooms");
  await expect(page.getByRole("heading", { name: "Race Rooms" })).toBeVisible();
  const roomCard = page.locator(`[data-room-slug="${ROOM_SLUG}"]`);
  await expect(roomCard).toBeVisible();
  await roomCard.click();
  await expect(page).toHaveURL(new RegExp(`/race-rooms/${ROOM_SLUG}(?:\\?.*)?$`));
  await expect(page.getByRole("heading", { name: "Day 3 Validation Room" })).toBeVisible();
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  await expect.poll(() => page.evaluate(() => (
    Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth
  ))).toBeLessThanOrEqual(1);

  const overflow = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  expect(overflow.document, `document overflow at ${overflow.viewport}px`).toBeLessThanOrEqual(overflow.viewport + 1);
  expect(overflow.body, `body overflow at ${overflow.viewport}px`).toBeLessThanOrEqual(overflow.viewport + 1);
}

test.describe.configure({ mode: "serial" });

test("runs a grounded replay through filtering, evidence, seek, and completion", async ({ page, request }) => {
  const browserErrors = collectBrowserErrors(page);
  await postRoomAction(request, "replay", { action: "restart" });
  await postRoomAction(request, "playback", { action: "pause" });

  await openValidationRoom(page);

  const roster = page.getByTestId("agent-roster");
  await expect(roster.locator(".agent-profile")).toHaveCount(5);
  for (const name of ["Mira Vale", "Theo Voss", "Lena Cross", "Arjun Reyes", "Nova"]) {
    await expect(roster.getByText(name, { exact: true })).toBeVisible();
  }

  const restart = page.getByTestId("restart-replay");
  await restart.click();
  await expect(page.getByTestId("playback-status")).toHaveText("Running");
  await page.getByTestId("toggle-playback").click();
  await expect(page.getByTestId("playback-status")).toHaveText("Paused");

  const speed = page.getByLabel("Playback speed");
  await speed.selectOption("8");
  await expect(speed).toHaveValue("8");
  await speed.selectOption("1");
  await page.getByTestId("toggle-playback").click();
  await expect(page.getByTestId("playback-status")).toHaveText("Running");

  const messages = page.getByTestId("room-message");
  await expect.poll(() => messages.count(), { timeout: 20_000 }).toBeGreaterThanOrEqual(10);
  await page.getByTestId("toggle-playback").click();
  await expect(page.getByTestId("playback-status")).toHaveText("Paused");

  const sequences = await messages.evaluateAll((nodes) => nodes.map((node) => Number(node.getAttribute("data-message-sequence"))));
  expect(sequences.length).toBeGreaterThanOrEqual(10);
  expect(sequences).toEqual([...sequences].sort((left, right) => left - right));
  expect(new Set(sequences).size).toBe(sequences.length);

  await page.getByLabel("Voice").selectOption("mira-vale");
  await page.getByLabel("Topic").selectOption("pit_stop");
  await expect(page).toHaveURL(/agent=mira-vale/);
  await expect(page).toHaveURL(/topic=pit_stop/);
  await expect(messages).toHaveCount(1);
  await expect(messages.first()).toHaveAttribute("data-agent-id", "mira-vale");
  await expect(messages.first()).toHaveAttribute("data-topic", "pit_stop");

  await messages.first().getByRole("button", { name: /Why this was said/ }).click();
  const drawer = page.getByTestId("evidence-drawer");
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("heading", { name: "Message evidence" })).toBeVisible();
  await expect(drawer.getByText("Trigger event")).toBeVisible();
  await expect(drawer.locator(".evidence-list article").first()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();

  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page).not.toHaveURL(/agent=/);
  await expect(page).not.toHaveURL(/topic=/);
  await expect.poll(() => messages.count()).toBeGreaterThanOrEqual(10);

  await page.getByRole("button", { name: /Seek/ }).click();
  const seekControls = page.locator("#replay-seek-controls");
  await seekControls.getByLabel("Lap").fill("11");
  await seekControls.getByRole("button", { name: "Go to lap" }).click();
  await expect(page.getByTestId("playback-status")).toHaveText("Paused");
  await expect(page.locator(".playback-bar__readout")).toContainText("Lap 11 / 12");

  await speed.selectOption("8");
  await page.getByTestId("toggle-playback").click();
  await expect(page.getByTestId("playback-status")).toHaveText("Replay complete", { timeout: 15_000 });
  await expect(page.locator('[data-testid="room-message"][data-topic="summary"]')).toHaveCount(2);
  await expect(page.locator('[data-testid="room-message"][data-message-type="summary"]')).toHaveCount(2);
  expect(browserErrors).toEqual([]);
});

for (const width of VIEWPORT_WIDTHS) {
  test(`keeps the index and room usable without horizontal overflow at ${width}px`, async ({ page }) => {
    const browserErrors = collectBrowserErrors(page);
    await page.setViewportSize({ width, height: width <= 768 ? 844 : 720 });
    await page.goto("/race-rooms");
    await expect(page.getByRole("heading", { name: "Race Rooms" })).toBeVisible();
    await expect(page.locator(`[data-room-slug="${ROOM_SLUG}"]`)).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.goto(`/race-rooms/${ROOM_SLUG}`);
    await expect(page.getByRole("heading", { name: "Day 3 Validation Room" })).toBeVisible();
    await expect(page.getByTestId("playback-controls")).toBeVisible();
    await expect(page.getByTestId("agent-roster")).toBeVisible();
    await expectNoHorizontalOverflow(page);

    if (width <= 860) {
      await expect(page.getByRole("button", { name: /5 agents in this room/ })).toBeVisible();
      await expect(page.getByRole("button", { name: /Race context & data/ })).toBeVisible();
    } else {
      await expect(page.getByTestId("agent-roster").locator(".agent-profile")).toHaveCount(5);
      await expect(page.getByRole("button", { name: /Race context & data/ })).toBeHidden();
    }

    const filterToggle = page.getByRole("button", { name: /Filter conversation/ });
    if (width <= 600) {
      await expect(filterToggle).toBeVisible();
      await expect(filterToggle).toHaveAttribute("aria-expanded", "false");
      await filterToggle.click();
      await expect(page.locator("#timeline-filters")).toBeVisible();
    } else {
      await expect(filterToggle).toBeHidden();
      await expect(page.locator("#timeline-filters")).toBeVisible();
    }
    if (width === 1280) {
      await page.getByText("Pipeline diagnostics", { exact: true }).click();
      await expect(page.locator(".diagnostic-counts")).toBeVisible();
    }
    await expectNoHorizontalOverflow(page);
    expect(browserErrors).toEqual([]);
  });
}
