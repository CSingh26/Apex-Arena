// SPDX-License-Identifier: AGPL-3.0-only
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API_BASE_URL = process.env.E2E_API_URL ?? "http://localhost:8764";
const DEVELOPMENT_FIXTURE_ENABLED = process.env.E2E_DEVELOPMENT_FIXTURE === "true";
const VIEWPORT_WIDTHS = [1440, 1280, 1024, 768, 390, 320] as const;

type SessionSummary = {
  session_type: string;
  display_name: string;
  scheduled_start: string;
  status: string;
  room_slug: string | null;
  eligibility: string;
  replay_available: boolean;
};

type EventWeekend = {
  event_slug: string;
  event_name: string;
  weekend_start: string;
  weekend_status: "live" | "completed" | "upcoming";
  is_sprint_weekend: boolean;
  sessions: SessionSummary[];
};

type EventResponse = { events: EventWeekend[]; total: number };
type RoomListResponse = { total: number };

function collectBrowserErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

async function eventCatalog(request: APIRequestContext): Promise<EventResponse> {
  const response = await request.get(`${API_BASE_URL}/api/v1/race-rooms/events?season=2026&limit=100`);
  expect(response.ok(), `event catalog returned HTTP ${response.status()}`).toBeTruthy();
  return response.json() as Promise<EventResponse>;
}

async function roomCount(request: APIRequestContext): Promise<number> {
  const response = await request.get(`${API_BASE_URL}/api/v1/race-rooms?season=2026&limit=100`);
  expect(response.ok(), `room catalog returned HTTP ${response.status()}`).toBeTruthy();
  return ((await response.json()) as RoomListResponse).total;
}

async function replayRoom(request: APIRequestContext): Promise<{ event: EventWeekend; session: SessionSummary }> {
  const catalog = await eventCatalog(request);
  for (const event of catalog.events) {
    const session = event.sessions.find((item) => item.room_slug && item.replay_available);
    if (session) return { event, session };
  }
  if (DEVELOPMENT_FIXTURE_ENABLED) {
    const fixture = await request.get(`${API_BASE_URL}/api/v1/race-rooms/day3-validation-room`);
    expect(fixture.ok(), "the isolated CI replay fixture should be available").toBeTruthy();
    return {
      event: {
        event_slug: "day3-validation",
        event_name: "Day 3 Validation",
        weekend_start: "2026-07-17T10:00:00Z",
        weekend_status: "completed",
        is_sprint_weekend: false,
        sessions: [],
      },
      session: {
        session_type: "RACE",
        display_name: "Day 3 Validation Room",
        scheduled_start: "2026-07-17T10:00:00Z",
        status: "completed",
        room_slug: "day3-validation-room",
        eligibility: "replay_ready",
        replay_available: true,
      },
    };
  }
  throw new Error("The production smoke test needs at least one completed replay-ready session");
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  await expect.poll(() => page.evaluate(() => (
    Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth
  ))).toBeLessThanOrEqual(1);
}

function expectAscending(values: number[]): void {
  expect(values).toEqual([...values].sort((left, right) => left - right));
}

test.describe.configure({ mode: "serial" });

test("introduces Apex Arena on a responsive, theme-aware landing page", async ({ page }) => {
  const browserErrors = collectBrowserErrors(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Every race has a story/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Not another timing screen." })).toBeVisible();
  await expect(page.getByRole("link", { name: "Experience" })).toHaveAttribute("href", "#experience");
  await expectNoHorizontalOverflow(page);

  const themeToggle = page.locator(".theme-toggle");
  const initialTheme = await page.locator("html").getAttribute("data-theme");
  await themeToggle.click();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", initialTheme ?? "");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: /Every race has a story/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Enter Race Rooms/ })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.getByRole("link", { name: /Enter Race Rooms/ }).click();
  await expect(page).toHaveURL(/\/race-rooms$/);
  await expect(page.getByRole("heading", { name: "Race Rooms" })).toBeVisible();
  expect(browserErrors).toEqual([]);
});

test("groups real standard and Sprint weekends in chronological public categories", async ({ page, request }) => {
  const browserErrors = collectBrowserErrors(page);
  const catalog = await eventCatalog(request);
  const completed = catalog.events.filter((event) => event.weekend_status === "completed");
  const upcoming = catalog.events.filter((event) => event.weekend_status === "upcoming");
  const standard = catalog.events.find((event) => !event.is_sprint_weekend);
  const sprint = catalog.events.find((event) => event.is_sprint_weekend);

  expect(catalog.events.length).toBeGreaterThan(0);
  expectAscending(completed.map((event) => Date.parse(event.weekend_start)));
  expectAscending(upcoming.map((event) => Date.parse(event.weekend_start)));
  expect(standard?.sessions.map((session) => session.session_type)).toEqual(["QUALIFYING", "RACE"]);
  expect(sprint?.sessions.map((session) => session.session_type)).toEqual([
    "SPRINT_QUALIFYING",
    "SPRINT",
    "QUALIFYING",
    "RACE",
  ]);
  for (const event of catalog.events) {
    expectAscending(event.sessions.map((session) => Date.parse(session.scheduled_start)));
  }

  await page.goto("/race-rooms");
  await expect(page.getByRole("heading", { name: "Live This Weekend" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Completed Events" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Upcoming Events" })).toBeVisible();
  await expect(page.getByText("Day 3 Validation Room")).toHaveCount(0);
  if (sprint) {
    await expect(page.locator(".event-card").filter({ hasText: sprint.event_name }).getByText("Sprint weekend")).toBeVisible();
  }
  const fixture = await request.get(`${API_BASE_URL}/api/v1/race-rooms/day3-validation-room`);
  expect(fixture.status()).toBe(DEVELOPMENT_FIXTURE_ENABLED ? 200 : 404);
  await expectNoHorizontalOverflow(page);
  expect(browserErrors).toEqual([]);
});

test("opens an upcoming schedule without creating a room and preserves browser history", async ({ page, request }) => {
  const browserErrors = collectBrowserErrors(page);
  const catalog = await eventCatalog(request);
  const event = catalog.events.find((item) => item.weekend_status === "upcoming" && item.sessions.length);
  expect(event, "the 2026 calendar should contain a future weekend").toBeTruthy();
  const session = event!.sessions[0];
  expect(session.room_slug).toBeNull();
  expect(session.eligibility).toBe("future_read_only");
  const before = await roomCount(request);

  await page.goto("/race-rooms");
  const card = page.locator(".event-card--upcoming").filter({ hasText: event!.event_name });
  await card.getByRole("button", { name: `View schedule for ${event!.event_name} ${session.display_name}` }).click();
  const dialog = page.getByRole("dialog", { name: event!.event_name });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("Room opens when session data becomes available.")).toBeVisible();
  await expect(page.locator(".timeline-card")).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`event=${encodeURIComponent(event!.event_slug)}`));
  expect(await roomCount(request)).toBe(before);

  await page.reload();
  await expect(page.getByRole("dialog", { name: event!.event_name })).toBeVisible();
  expect(await roomCount(request)).toBe(before);
  await page.goBack();
  await expect(page.getByRole("dialog", { name: event!.event_name })).toHaveCount(0);
  await expect(page).not.toHaveURL(/event=/);
  expect(await roomCount(request)).toBe(before);
  expect(browserErrors).toEqual([]);
});

test("keeps a replay conversation compact, inspectable, and session-aware", async ({ page, request }) => {
  const browserErrors = collectBrowserErrors(page);
  const { session } = await replayRoom(request);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`/race-rooms/${session.room_slug}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("img", { name: /2026 circuit layout/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Session conversation" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Session timeline" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Track dossier" })).toBeVisible();
  if (!DEVELOPMENT_FIXTURE_ENABLED || session.room_slug !== "day3-validation-room") {
    await expect(page.locator(".circuit-records > div")).toHaveCount(3);
  }
  await expect(page.getByRole("heading", { name: "Track weather" })).toBeVisible();
  await expect(page.locator(".weather-card__notice")).toBeVisible();
  await expect(page.getByTestId("playback-controls")).toBeVisible();
  await expect(page.getByTestId("agent-roster").locator(".agent-profile")).toHaveCount(0);
  await page.getByTestId("agent-roster").getByRole("button", { name: /agents in this room/ }).click();
  await expect(page.getByTestId("agent-roster").locator(".agent-profile")).toHaveCount(5);
  await expect(page.locator(".room-context-technical")).not.toHaveAttribute("open");

  const messages = page.getByTestId("room-message");
  await expect.poll(() => messages.count(), { timeout: 20_000 }).toBeGreaterThan(0);
  await expect(messages.first()).toHaveAttribute("data-message-side", /left|right|host/);
  const conversation = page.getByRole("log", { name: "Agent conversation" });
  await expect(conversation).toBeVisible();
  await expect.poll(() => conversation.evaluate((element) => element.scrollHeight >= element.clientHeight)).toBeTruthy();
  const copy = (await messages.first().locator(".message__body > p").innerText()).trim();
  expect(copy.length).toBeLessThanOrEqual(420);
  await messages.first().getByRole("button", { name: /See the data behind/ }).click();
  await expect(page.getByTestId("evidence-drawer")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("evidence-drawer")).toBeHidden();
  if (session.session_type.includes("QUALIFYING")) {
    await expect(page.locator(".room-header")).not.toContainText("Lap 0 / 0");
  }
  await expectNoHorizontalOverflow(page);
  expect(browserErrors).toEqual([]);
});

for (const width of VIEWPORT_WIDTHS) {
  test(`keeps navigation, grouped events, and a real room usable at ${width}px`, async ({ page, request }) => {
    const browserErrors = collectBrowserErrors(page);
    const { session } = await replayRoom(request);
    await page.setViewportSize({ width, height: width <= 768 ? 844 : 800 });
    await page.goto("/race-rooms");
    await expect(page.getByRole("heading", { name: "Race Rooms" })).toBeVisible();
    await expect(page.locator(".app-nav")).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    await expect(page.locator(".app-nav")).toHaveCSS("position", "absolute");
    await expectNoHorizontalOverflow(page);

    const menuButton = page.getByRole("button", { name: "Open navigation menu" });
    if (width <= 800) {
      await expect(menuButton).toBeVisible();
      await menuButton.click();
      await expect(page.getByRole("dialog", { name: "Mobile navigation" })).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog", { name: "Mobile navigation" })).toHaveCount(0);
      await expect(menuButton).toBeFocused();
    } else {
      await expect(menuButton).toBeHidden();
      await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
    }
    if (width <= 600) {
      const filterToggle = page.getByRole("button", { name: /All events|Active/ });
      await expect(filterToggle).toBeVisible();
      await filterToggle.click();
      await expect(page.locator("#event-filter-fields")).toBeVisible();
    }

    await page.goto(`/race-rooms/${session.room_slug}`);
    await expect(page.getByRole("heading", { name: "Session conversation" })).toBeVisible();
    await expect(page.getByTestId("agent-roster")).toBeVisible();
    if (width <= 860) {
      await expect(page.getByRole("button", { name: /Session details/ })).toBeVisible();
    }
    await expectNoHorizontalOverflow(page);
    expect(browserErrors).toEqual([]);
  });
}
