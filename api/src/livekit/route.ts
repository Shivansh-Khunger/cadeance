import { Hono } from "hono";
import { AccessToken } from "livekit-server-sdk";
import { RoomAgentDispatch, RoomConfiguration } from "@livekit/protocol";
import { z } from "zod";

import { env } from "./../config/env";

const AGENT_NAME = "cadence";
const TOKEN_TTL = "10m";

const bodySchema = z.object({
  room_name: z.string().min(1).optional(),
  participant_identity: z.string().min(1).optional(),
  participant_name: z.string().min(1).optional(),
});

export const livekit = new Hono();

livekit.post("/connection-details", async (c) => {
  const raw = await c.req.json().catch(() => ({}));
  const parsed = bodySchema.safeParse(raw);
  if (!parsed.success) {
    return c.json({ error: "Invalid request body" }, 400);
  }

  const roomName = parsed.data.room_name ?? `room-${crypto.randomUUID()}`;
  const identity =
    parsed.data.participant_identity ?? `user-${crypto.randomUUID()}`;
  const name = parsed.data.participant_name ?? "User";

  const at = new AccessToken(env.LIVEKIT_API_KEY, env.LIVEKIT_API_SECRET, {
    identity,
    name,
    ttl: TOKEN_TTL,
  });

  at.addGrant({
    roomJoin: true,
    room: roomName,
    canPublish: true,
    canSubscribe: true,
    canPublishData: true,
  });

  at.roomConfig = new RoomConfiguration({
    agents: [new RoomAgentDispatch({ agentName: AGENT_NAME })],
  });

  const participantToken = await at.toJwt();

  return c.json(
    {
      server_url: env.LIVEKIT_URL,
      participant_token: participantToken,
      room_name: roomName,
      participant_name: name,
    },
    201,
  );
});
