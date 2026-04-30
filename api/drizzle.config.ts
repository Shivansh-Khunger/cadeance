import 'dotenv/config';
import { defineConfig } from 'drizzle-kit';

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
