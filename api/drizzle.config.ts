import { config } from 'dotenv';
import { defineConfig } from 'drizzle-kit';

// drizzle-kit runs under Node (its bin has a node shebang), so Bun's env
// auto-loading doesn't reach it. Load the same file the API reads locally:
// api/.env.local, a symlink to the root .env.local. In production or CI the
// file is absent and DATABASE_URL comes from the real environment.
config({ path: '.env.local', quiet: true });

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) {
  throw new Error('DATABASE_URL is required for drizzle-kit');
}

export default defineConfig({
  out: './drizzle',
  schema: './src/db/schema',
  dialect: 'postgresql',
  dbCredentials: {
    url: databaseUrl,
  },
  // Limit introspection to our tables. Without this, drizzle-kit 0.31.x
  // crashes pulling CHECK constraints from unrelated tables that may share
  // this database (extensions, other apps' schemas).
  tablesFilter: ['users', 'appointments', 'conversations'],
  schemaFilter: ['public'],
});
