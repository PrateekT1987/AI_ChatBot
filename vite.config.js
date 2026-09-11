import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const zenApiKey = env.VITE_OPENCODE_API_KEY || 'public'
  const siliconflowApiKey = env.VITE_SILICONFLOW_API_KEY || ''
  const geminiApiKey = env.VITE_GEMINI_API_KEY || ''

  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api/zen': {
          target: 'https://opencode.ai',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api\/zen/, '/zen/v1'),
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              // Use the real key from .env (opencode.ai/auth). The free tier
              // rejects the placeholder 'public', so this must be a valid key.
              proxyReq.setHeader('Authorization', `Bearer ${zenApiKey}`);

              // These headers identify the request as coming from an OpenCode
              // client; the free-tier models require them.
              proxyReq.setHeader('x-opencode-client', 'cli');
              proxyReq.setHeader('x-opencode-session', `ses_${Date.now()}`);
              proxyReq.setHeader('User-Agent', 'opencode/1.15.5');
            });
          },
        },
        '/api/siliconflow': {
          target: 'https://api.siliconflow.com',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api\/siliconflow/, '/v1'),
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              proxyReq.setHeader('Authorization', `Bearer ${siliconflowApiKey}`);
            });
          },
        },
        '/api/gemini': {
          target: 'https://generativelanguage.googleapis.com',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api\/gemini/, '/v1beta/openai'),
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              // A Google AI Studio key (aistudio.google.com) is a plain
              // OpenAI-compatible bearer token on this endpoint.
              proxyReq.setHeader('Authorization', `Bearer ${geminiApiKey}`);
            });
          },
        },
      },
    },
  }
})
