import assert from 'node:assert/strict';
import test from 'node:test';

import { filterSlashCommands, type SlashCommand } from './useSlashCommands';

const commands: SlashCommand[] = [
  { name: '/review', description: 'Claude review command' },
  { name: '$browser:control-in-app-browser', description: 'Codex browser skill', type: 'skill' },
  { name: '$research', description: 'Codex research skill', type: 'skill' },
];

test('Codex dollar-prefixed skills are filterable by their native invocation', () => {
  assert.deepEqual(
    filterSlashCommands(commands, '$browser').map((command) => command.name),
    ['$browser:control-in-app-browser'],
  );
});

test('sigil-free menu search considers both Claude and Codex commands', () => {
  assert.deepEqual(
    filterSlashCommands(commands, 'research').map((command) => command.name),
    ['$research'],
  );
  assert.deepEqual(
    filterSlashCommands(commands, 'review').map((command) => command.name),
    ['/review'],
  );
});
