import { browserUseService } from '@/modules/browser-use/browser-use.service.js';

const result = await browserUseService.installRuntime();
process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
if (!result.success) process.exitCode = 1;
