// SPDX-License-Identifier: AGPL-3.0-only
import { RoomExperience } from "@/components/race-rooms/room-experience";

export default async function RoomPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <RoomExperience key={slug} slug={slug} />;
}
