import { z } from "zod";

const schema = z.object({
  PORT: z.coerce.number().int().positive().default(4000),
  DATABASE_URL: z.url(),
  CORS_ORIGIN: z.string().default("http://localhost:3000"),
  LIVEKIT_URL: z.string().min(1),
  LIVEKIT_API_KEY: z.string().min(1),
  LIVEKIT_API_SECRET: z.string().min(1),
});

const parsed = schema.safeParse(process.env);

if (!parsed.success) {
  console.error(
    "Invalid environment variables:",
    parsed.error,
  );
  throw new Error("Invalid environment variables");
}

export const env = parsed.data;
