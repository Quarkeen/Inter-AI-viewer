import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

await build({
  absWorkingDir: fileURLToPath(new URL('..', import.meta.url)),
  entryPoints: ['src/lib/gaze.worker.ts'], outfile: 'public/preflight/gaze-worker.js',
  bundle: true, format: 'iife', platform: 'browser', target: 'es2022', minify: true,
  legalComments: 'eof',
});
