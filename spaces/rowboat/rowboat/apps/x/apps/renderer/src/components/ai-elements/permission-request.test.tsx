// Modified for VibeMind (Flissel/rowboat vibemind): human-only requests show no session/always scope (#330, review M4).
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { PermissionRequest } from './permission-request';

afterEach(cleanup);

const toolCall = {
  type: 'tool-call' as const,
  toolCallId: 'c1',
  toolName: 'file-writeText',
  arguments: { path: 'config/mcp.json', data: '{}' },
};
const filePermission = {
  kind: 'file' as const,
  operation: 'write' as const,
  paths: ['C:/ws/config/mcp.json'],
  pathPrefix: 'C:/ws/config',
};
const noop = () => undefined;

function scopeTriggers(): number {
  // The scope dropdown is the only button besides Allow, Deny and the summary.
  return screen.getAllByRole('button').length - 3;
}

describe('PermissionRequest scope actions', () => {
  it('offers the session / always scope for an ordinary request', () => {
    render(
      <PermissionRequest
        toolCall={toolCall}
        permission={filePermission}
        onApprove={noop}
        onApproveSession={noop}
        onApproveAlways={noop}
        onDeny={noop}
      />,
    );
    expect(scopeTriggers()).toBe(1);
    expect(screen.queryByText(/Protected path/)).not.toBeInTheDocument();
  });

  it('hides the scope for a human-only request and says why', () => {
    render(
      <PermissionRequest
        toolCall={toolCall}
        permission={{ ...filePermission, humanOnly: true }}
        onApprove={noop}
        onApproveSession={noop}
        onApproveAlways={noop}
        onDeny={noop}
      />,
    );
    expect(scopeTriggers()).toBe(0);
    expect(screen.getByText(/Protected path: only you can approve this/)).toBeInTheDocument();
  });
});
