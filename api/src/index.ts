import { Hono } from 'hono'
import { cors } from 'hono/cors'
import { adminRouter } from './admin/route';
import { appointmentsRouter } from './appointments/route';
import { env } from './config/env';
import { livekit } from './livekit/route';
import { connectDB } from './config/database';

const app = new Hono()
connectDB()

app.use('*', cors({
  origin: env.CORS_ORIGIN,
  allowMethods: ['GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS'],
  allowHeaders: ['Content-Type', 'Authorization'],
}));

app.get('/', (c) => {
  return c.text('Hello Hono!')
})

app.route('/', livekit);
app.route('/', appointmentsRouter);
app.route('/admin', adminRouter);

export default {
  port: env.PORT,
  fetch: app.fetch,
};
